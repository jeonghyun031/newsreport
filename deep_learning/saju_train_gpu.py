"""
deep_learning/saju_train_gpu.py
==============================
GPU PC에서 실행하는 딥러닝 학습 스크립트

[실행 조건]
  - AWS MySQL training_jobs 테이블에 status='READY' 잡이 있어야 함
  - GPU PC에 torch, pytorch-tabnet 설치 필요

[실행 방법]
  git pull                    # 최신 코드 가져오기
  cp /path/to/.env .env       # .env 복사 (DB 접속 정보)
  pip install -r requirements.txt
  python deep_learning/saju_train_gpu.py

[모델 저장]
  - 학습된 모델 → AWS MySQL model_weights 테이블 (BLOB)
  - 로컬 백업 → models/saju_tabnet_YYYYMMDD.pt

[학습 목표]
  - 투수의 내년 시즌 ERA 예측 (회귀)
  - 에이스 여부 분류 (ERA < 3.5)
  - 학습 완료 후 saju.py가 예측값을 프롬프트에 자동 주입
"""

import os
import sys
import json
import pickle
import pymysql
import numpy as np
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── GPU 환경 확인 ─────────────────────────────────────────────────────────────
try:
    import torch
    GPU_AVAILABLE = torch.cuda.is_available()
    print(f"🖥️  PyTorch: {torch.__version__}")
    print(f"🚀 GPU: {'사용 가능 (' + torch.cuda.get_device_name(0) + ')' if GPU_AVAILABLE else '없음 (CPU 모드)'}")
except ImportError:
    print("❌ torch 미설치. pip install torch torchvision")
    sys.exit(1)

try:
    import pandas as pd
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import mean_squared_error, accuracy_score
    ML_OK = True
except ImportError:
    print("❌ scikit-learn 또는 pandas 미설치. pip install scikit-learn pandas")
    sys.exit(1)

try:
    from pytorch_tabnet.tab_model import TabNetRegressor, TabNetClassifier
    TABNET_OK = True
    print("✅ pytorch-tabnet 로드 완료")
except ImportError:
    TABNET_OK = False
    print("⚠️ pytorch-tabnet 미설치 → PyTorch MLP 폴백 모드")


# ── .env 로드 ─────────────────────────────────────────────────────────────────
def load_env(filepath=".env"):
    if not os.path.exists(filepath) and filepath == ".env":
        parent_env = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(parent_env):
            filepath = parent_env
    if not os.path.exists(filepath):
        print("⚠️ .env 파일 없음! DB 접속 정보를 환경변수로 직접 설정하거나 .env를 복사하세요.")
        return
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

load_env()


# ── DB 연결 ───────────────────────────────────────────────────────────────────
def get_conn():
    return pymysql.connect(
        host=os.getenv("AWS_RDS_ENDPOINT", ""),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "admin"),
        password=os.getenv("DB_PASSWORD", ""),
        database="statistics_db",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=15,
    )


# ── READY 잡 확인 ─────────────────────────────────────────────────────────────
def check_ready_job(conn) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, job_name, pitcher_rows, hitter_rows, notes
            FROM training_jobs
            WHERE status = 'READY'
            ORDER BY id DESC LIMIT 1
        """)
        return cur.fetchone()


def update_job_status(conn, job_id: int, status: str, notes: str = ""):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE training_jobs
            SET status = %s, notes = %s, updated_at = NOW()
            WHERE id = %s
        """, (status, notes, job_id))
    conn.commit()


# ── training_features 로드 ────────────────────────────────────────────────────
FEATURE_COLS = ["era", "whip", "so_per_9", "bb_per_9", "hr_per_9", "k_bb_ratio", "era_z", "opp_ops", "opp_avg"]

def load_features(conn) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """training_features → X(피처), y_reg(ERA), y_cls(에이스여부) 반환"""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT {', '.join(FEATURE_COLS)}, label_era, label_ace
            FROM training_features
        """)
        rows = cur.fetchall()

    if not rows:
        raise RuntimeError("training_features 테이블이 비어있습니다. saju_prepare_training.py를 먼저 실행하세요.")

    df = pd.DataFrame(rows)
    df = df.fillna(df.median(numeric_only=True))

    X     = df[FEATURE_COLS].values.astype(np.float32)
    y_reg = df["label_era"].values.astype(np.float32)
    y_cls = df["label_ace"].values.astype(np.int64)

    print(f"📊 학습 데이터: {X.shape[0]}개 샘플 × {X.shape[1]}개 피처")
    return X, y_reg, y_cls


# ── model_weights 테이블 초기화 ───────────────────────────────────────────────
def ensure_model_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS model_weights (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                model_name  VARCHAR(100),
                model_type  VARCHAR(50),
                val_score   FLOAT,
                weights     LONGBLOB,
                scaler      LONGBLOB,
                feature_cols JSON,
                trained_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                gpu_info    VARCHAR(200)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


# ── 모델 저장 (MySQL BLOB + 로컬 백업) ───────────────────────────────────────
def save_model(conn, model, scaler, model_name: str, val_score: float, model_type: str):
    weights_blob  = pickle.dumps(model)
    scaler_blob   = pickle.dumps(scaler)
    gpu_info      = torch.cuda.get_device_name(0) if GPU_AVAILABLE else "CPU"

    # MySQL 저장
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO model_weights
                (model_name, model_type, val_score, weights, scaler, feature_cols, gpu_info)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            model_name,
            model_type,
            val_score,
            weights_blob,
            scaler_blob,
            json.dumps(FEATURE_COLS),
            gpu_info,
        ))
    conn.commit()
    print(f"  💾 MySQL model_weights 저장 완료 (검증 점수: {val_score:.4f})")

    # 로컬 백업
    Path("models").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    local_path = f"models/{model_name}_{ts}.pkl"
    with open(local_path, "wb") as f:
        pickle.dump({"model": model, "scaler": scaler, "features": FEATURE_COLS}, f)
    print(f"  📁 로컬 백업: {local_path}")


# ── TabNet 학습 ───────────────────────────────────────────────────────────────
def train_tabnet(X_tr, y_tr, X_val, y_val, task: str = "regression"):
    """TabNet 모델 학습"""
    device = "cuda" if GPU_AVAILABLE else "cpu"

    if task == "regression":
        model = TabNetRegressor(
            n_d=16, n_a=16, n_steps=5,
            gamma=1.3, n_independent=2, n_shared=2,
            device_name=device,
            verbose=10,
        )
        y_tr_  = y_tr.reshape(-1, 1)
        y_val_ = y_val.reshape(-1, 1)
        model.fit(
            X_tr, y_tr_,
            eval_set=[(X_val, y_val_)],
            eval_name=["val"],
            eval_metric=["mse"],
            max_epochs=200,
            patience=20,
            batch_size=256,
        )
        preds = model.predict(X_val).flatten()
        score = float(mean_squared_error(y_val, preds, squared=False))  # RMSE
        print(f"  📈 TabNet 회귀 검증 RMSE: {score:.4f}")
        return model, score

    else:  # classification
        model = TabNetClassifier(
            n_d=16, n_a=16, n_steps=5,
            gamma=1.3, n_independent=2, n_shared=2,
            device_name=device,
            verbose=10,
        )
        model.fit(
            X_tr, y_tr,
            eval_set=[(X_val, y_val)],
            eval_name=["val"],
            eval_metric=["accuracy"],
            max_epochs=200,
            patience=20,
            batch_size=256,
        )
        preds = model.predict(X_val)
        score = float(accuracy_score(y_val, preds))
        print(f"  📈 TabNet 분류 검증 Accuracy: {score:.4f}")
        return model, score


# ── PyTorch MLP 폴백 ─────────────────────────────────────────────────────────
class SimpleMLP(torch.nn.Module):
    def __init__(self, input_dim: int, task: str = "regression"):
        super().__init__()
        self.task = task
        self.net = torch.nn.Sequential(
            torch.nn.Linear(input_dim, 64),
            torch.nn.ReLU(),
            torch.nn.Dropout(0.3),
            torch.nn.Linear(64, 32),
            torch.nn.ReLU(),
            torch.nn.Linear(32, 1),
        )

    def forward(self, x):
        return self.net(x)


def train_mlp(X_tr, y_tr, X_val, y_val, task: str = "regression"):
    device = torch.device("cuda" if GPU_AVAILABLE else "cpu")
    model  = SimpleMLP(X_tr.shape[1], task).to(device)
    opt    = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    loss_fn = torch.nn.MSELoss() if task == "regression" else torch.nn.BCEWithLogitsLoss()

    X_tr_t  = torch.FloatTensor(X_tr).to(device)
    y_tr_t  = torch.FloatTensor(y_tr).unsqueeze(1).to(device)
    X_val_t = torch.FloatTensor(X_val).to(device)
    y_val_t = torch.FloatTensor(y_val).unsqueeze(1).to(device)

    best_val_loss = float("inf")
    patience, patience_cnt = 30, 0
    for epoch in range(300):
        model.train()
        opt.zero_grad()
        pred = model(X_tr_t)
        loss = loss_fn(pred, y_tr_t)
        loss.backward()
        opt.step()

        model.eval()
        with torch.no_grad():
            val_pred = model(X_val_t)
            val_loss = loss_fn(val_pred, y_val_t).item()

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_cnt  = 0
        else:
            patience_cnt += 1
            if patience_cnt >= patience:
                print(f"  ⏹ Early stopping at epoch {epoch}")
                break

        if (epoch + 1) % 50 == 0:
            print(f"  Epoch {epoch+1:>3} | train_loss: {loss.item():.4f} | val_loss: {val_loss:.4f}")

    score = float(np.sqrt(best_val_loss)) if task == "regression" else 1.0 - best_val_loss
    return model, score


# ── 메인 학습 루프 ────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"  🚀 KBO 사주 딥러닝 학습 시작 (GPU PC)")
    print(f"  GPU: {'✅ ' + torch.cuda.get_device_name(0) if GPU_AVAILABLE else '❌ CPU 모드'}")
    print(f"{'='*60}\n")

    # 1) DB 연결 및 READY 잡 확인
    try:
        conn = get_conn()
    except Exception as e:
        print(f"❌ DB 연결 실패: {e}")
        print("⚠️ .env 파일이 있는지, AWS RDS 접속이 가능한지 확인하세요.")
        return

    job = check_ready_job(conn)
    if not job:
        print("⚠️ training_jobs 테이블에 READY 상태인 잡이 없습니다.")
        print("   현재 랩탑에서 순서대로 실행하세요:")
        print("   1) python kbo_crawler.py")
        print("   2) python deep_learning/saju_prepare_training.py")
        conn.close()
        return

    print(f"✅ READY 잡 발견: [{job['job_name']}] → 학습을 시작합니다.")
    update_job_status(conn, job["id"], "TRAINING", "GPU PC에서 학습 중...")
    ensure_model_table(conn)

    # 2) 피처 로드
    try:
        X, y_reg, y_cls = load_features(conn)
    except RuntimeError as e:
        print(f"❌ {e}")
        update_job_status(conn, job["id"], "FAILED", str(e))
        conn.close()
        return

    # 3) 전처리 (스케일링)
    X_tr, X_val, yr_tr, yr_val, yc_tr, yc_val = train_test_split(
        X, y_reg, y_cls, test_size=0.2, random_state=42
    )
    scaler = StandardScaler()
    X_tr  = scaler.fit_transform(X_tr)
    X_val = scaler.transform(X_val)

    # 4) 회귀 모델 학습 (ERA 예측)
    print(f"\n[1/2] 🏋️ ERA 예측 회귀 모델 학습 중...")
    try:
        if TABNET_OK:
            reg_model, reg_score = train_tabnet(X_tr, yr_tr, X_val, yr_val, task="regression")
            model_type = "TabNetRegressor"
        else:
            reg_model, reg_score = train_mlp(X_tr, yr_tr, X_val, yr_val, task="regression")
            model_type = "SimpleMLP_Regressor"
        save_model(conn, reg_model, scaler, "saju_era_regressor", reg_score, model_type)
    except Exception as e:
        print(f"  ❌ 회귀 학습 실패: {e}")

    # 5) 분류 모델 학습 (에이스 여부)
    print(f"\n[2/2] 🏋️ 에이스 여부 분류 모델 학습 중...")
    try:
        if TABNET_OK:
            cls_model, cls_score = train_tabnet(X_tr, yc_tr, X_val, yc_val, task="classification")
            model_type_cls = "TabNetClassifier"
        else:
            cls_model, cls_score = train_mlp(X_tr, yc_tr.astype(np.float32), yc_val.astype(np.float32), task="classification")
            model_type_cls = "SimpleMLP_Classifier"
        save_model(conn, cls_model, scaler, "saju_ace_classifier", cls_score, model_type_cls)
    except Exception as e:
        print(f"  ❌ 분류 학습 실패: {e}")

    # 6) 완료
    update_job_status(
        conn, job["id"], "DONE",
        f"학습 완료. ERA RMSE={reg_score:.4f}, ACE Acc={cls_score:.4f}. "
        f"GPU={torch.cuda.get_device_name(0) if GPU_AVAILABLE else 'CPU'}"
    )
    conn.close()

    print(f"\n{'='*60}")
    print(f"  🎉 딥러닝 학습 완료!")
    print(f"  모델이 AWS MySQL model_weights 테이블에 저장되었습니다.")
    print(f"  이제 saju.py가 자동으로 예측값을 사주풀이에 반영합니다.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
