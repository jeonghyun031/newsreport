"""
kbo_crawler.py
==============
KBO 공식 홈페이지 역대 투수·타자 스탯 크롤러 (2019 ~ 현재)

[실행 흐름]
  1. Playwright로 KBO 홈페이지 접속
  2. 연도별(2019~현재) 투수/타자 스탯 수집 (모든 페이지)
  3. AWS MySQL(articel_db)에 저장
  4. 학습 준비 완료 → training_jobs 테이블에 READY 등록 후 종료
      ※ 실제 딥러닝 학습은 GPU PC에서 python deep_learning/saju_train_gpu.py 실행

[필요 패키지]
  pip install playwright beautifulsoup4 pymysql
  playwright install chromium
"""

import os
import sys
import time
import json
import pymysql
from datetime import datetime

# Windows 콘솔 한글 깨짐 방지
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── 선택적 임포트 ─────────────────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False
    print("❌ playwright 미설치. 아래 명령 실행 후 재시도:\n   pip install playwright && playwright install chromium")

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False
    print("❌ beautifulsoup4 미설치. pip install beautifulsoup4")

# ── 설정 ──────────────────────────────────────────────────────────────────────
START_YEAR = 2019
END_YEAR   = datetime.now().year

PITCHER_URL = "https://www.koreabaseball.com/Record/Player/PitcherBasic/Basic1.aspx"
HITTER_URL  = "https://www.koreabaseball.com/Record/Player/HitterBasic/Basic1.aspx"

PITCHER_COLS = [
    "player_name", "team", "era", "games", "wins", "losses",
    "saves", "holds", "win_pct", "tbf", "ip", "hits",
    "hr", "bb", "hbp", "so", "wp", "bk", "runs", "er", "whip"
]
HITTER_COLS = [
    "player_name", "team", "avg", "games", "pa", "ab",
    "runs", "hits", "b2", "b3", "hr", "tb",
    "rbi", "sb", "cs", "bb", "hbp", "so", "gdp", "slg", "obp", "ops"
]

# ── .env 로드 ─────────────────────────────────────────────────────────────────
def load_env(filepath=".env"):
    if not os.path.exists(filepath):
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
        host=os.getenv("AWS_RDS_ENDPOINT", "database-1.cf0ecym6emk4.ap-southeast-2.rds.amazonaws.com"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "admin"),
        password=os.getenv("DB_PASSWORD", "12341234"),
        database="statistics_db",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )

# ── 테이블 초기화 ─────────────────────────────────────────────────────────────
def init_tables(conn):
    with conn.cursor() as cur:
        # 투수 스탯
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kbo_pitcher_stats (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                season      INT NOT NULL,
                player_name VARCHAR(50),
                team        VARCHAR(30),
                era         FLOAT,
                games       INT,
                wins        INT,
                losses      INT,
                saves       INT,
                holds       INT,
                win_pct     FLOAT,
                tbf         INT,
                ip          VARCHAR(20),
                hits        INT,
                hr          INT,
                bb          INT,
                hbp         INT,
                so          INT,
                wp          INT,
                bk          INT,
                runs        INT,
                er          INT,
                whip        FLOAT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_pitcher (season, player_name, team)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        # 타자 스탯
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kbo_hitter_stats (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                season      INT NOT NULL,
                player_name VARCHAR(50),
                team        VARCHAR(30),
                avg         FLOAT,
                games       INT,
                pa          INT,
                ab          INT,
                runs        INT,
                hits        INT,
                b2          INT,
                b3          INT,
                hr          INT,
                tb          INT,
                rbi         INT,
                sb          INT,
                cs          INT,
                bb          INT,
                hbp         INT,
                so          INT,
                gdp         INT,
                slg         FLOAT,
                obp         FLOAT,
                ops         FLOAT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_hitter (season, player_name, team)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        # 학습 잡 제어 테이블
        cur.execute("""
            CREATE TABLE IF NOT EXISTS training_jobs (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                job_name   VARCHAR(100),
                status     ENUM('COLLECTING','READY','TRAINING','DONE','FAILED') DEFAULT 'COLLECTING',
                pitcher_rows INT DEFAULT 0,
                hitter_rows  INT DEFAULT 0,
                notes      TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()
    print("✅ DB 테이블 준비 완료 (kbo_pitcher_stats / kbo_hitter_stats / training_jobs)")


# ── 공통: HTML 테이블 파싱 ────────────────────────────────────────────────────
def parse_table(html: str, col_names: list) -> list[dict]:
    """
    KBO 기록실 테이블 HTML → dict 리스트
    첫 번째 <table> 혹은 id="tblRecord" 테이블 파싱
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "tblRecord"}) or soup.find("table", class_="tData")
    if not table:
        # fallback: 첫 번째 table
        table = soup.find("table")
    if not table:
        return []

    rows = table.find_all("tr")
    records = []
    for row in rows:
        cells = row.find_all("td")
        if not cells or len(cells) < 3:
            continue
        vals = [c.get_text(strip=True) for c in cells]
        rec = {}
        for i, col in enumerate(col_names):
            rec[col] = vals[i] if i < len(vals) else None
        records.append(rec)
    return records


# ── 공통: 수치 안전 변환 ──────────────────────────────────────────────────────
def to_float(v):
    try:
        return float(str(v).replace(",", "").strip()) if v else None
    except Exception:
        return None

def to_int(v):
    try:
        return int(str(v).replace(",", "").strip()) if v else None
    except Exception:
        return None


# ── Playwright: 연도 + 페이지 전체 크롤 ──────────────────────────────────────
def crawl_page(browser, url: str, year: int, col_names: list, stat_type: str) -> list[dict]:
    """
    지정 URL의 특정 연도 스탯 전 페이지를 수집합니다.
    """
    page = browser.new_page()
    all_records = []

    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        time.sleep(1)

        # ── 연도 선택 ───────────────────────────────────────────────────────
        season_sel = None
        for sel in ["#ddlSeason", "select[name*='Season']", "select[id*='Season']"]:
            try:
                page.wait_for_selector(sel, timeout=3000)
                season_sel = sel
                break
            except Exception:
                continue

        if season_sel:
            page.select_option(season_sel, str(year))
            page.wait_for_load_state("networkidle")
            time.sleep(1.5)
        else:
            print(f"  ⚠️ 연도 선택 드롭다운을 찾지 못했습니다. ({year})")

        # ── 페이지 루프 ─────────────────────────────────────────────────────
        current_page = 1
        while True:
            html = page.content()
            records = parse_table(html, col_names)

            if not records:
                print(f"  ⚠️ {year}년 {stat_type} {current_page}p: 데이터 없음")
                break

            # season 필드 추가
            for r in records:
                r["season"] = year
            all_records.extend(records)
            print(f"  📊 {year}년 {stat_type} {current_page}p: {len(records)}명 수집")

            # ── 다음 페이지 버튼 탐색 ──────────────────────────────────────
            # KBO 페이지네이션: 숫자 버튼 + "다음" 링크
            next_btn = None
            for sel in ["a:has-text('다음')", "a.btn-next", ".paging a:last-child"]:
                try:
                    el = page.locator(sel).last
                    href = el.get_attribute("href") or ""
                    onclick = el.get_attribute("onclick") or ""
                    if href == "#" or "doPostBack" in onclick or "javascript" in href:
                        next_btn = el
                        break
                    elif href and href != "#":
                        next_btn = el
                        break
                except Exception:
                    continue

            if next_btn:
                try:
                    next_btn.click()
                    page.wait_for_load_state("networkidle")
                    time.sleep(1)
                    current_page += 1
                except Exception:
                    break
            else:
                break  # 마지막 페이지

    except PWTimeoutError:
        print(f"  ⚠️ Timeout: {year}년 {stat_type}")
    except Exception as e:
        print(f"  ❌ 크롤링 오류 ({year}년 {stat_type}): {e}")
    finally:
        page.close()

    return all_records


# ── DB 저장: 투수 ─────────────────────────────────────────────────────────────
def save_pitchers(conn, records: list[dict]) -> int:
    saved = 0
    with conn.cursor() as cur:
        for r in records:
            try:
                cur.execute("""
                    INSERT INTO kbo_pitcher_stats
                        (season, player_name, team, era, games, wins, losses, saves, holds,
                         win_pct, tbf, ip, hits, hr, bb, hbp, so, wp, bk, runs, er, whip)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        era=VALUES(era), games=VALUES(games), wins=VALUES(wins),
                        so=VALUES(so), whip=VALUES(whip), updated_at=NOW()
                """, (
                    r.get("season"),
                    r.get("player_name"),
                    r.get("team"),
                    to_float(r.get("era")),
                    to_int(r.get("games")),
                    to_int(r.get("wins")),
                    to_int(r.get("losses")),
                    to_int(r.get("saves")),
                    to_int(r.get("holds")),
                    to_float(r.get("win_pct")),
                    to_int(r.get("tbf")),
                    r.get("ip"),
                    to_int(r.get("hits")),
                    to_int(r.get("hr")),
                    to_int(r.get("bb")),
                    to_int(r.get("hbp")),
                    to_int(r.get("so")),
                    to_int(r.get("wp")),
                    to_int(r.get("bk")),
                    to_int(r.get("runs")),
                    to_int(r.get("er")),
                    to_float(r.get("whip")),
                ))
                saved += 1
            except Exception as e:
                print(f"    ⚠️ 투수 저장 실패 ({r.get('player_name')}): {e}")
    conn.commit()
    return saved


# ── DB 저장: 타자 ─────────────────────────────────────────────────────────────
def save_hitters(conn, records: list[dict]) -> int:
    saved = 0
    with conn.cursor() as cur:
        for r in records:
            try:
                cur.execute("""
                    INSERT INTO kbo_hitter_stats
                        (season, player_name, team, avg, games, pa, ab, runs, hits,
                         b2, b3, hr, tb, rbi, sb, cs, bb, hbp, so, gdp, slg, obp, ops)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        avg=VALUES(avg), games=VALUES(games), hr=VALUES(hr),
                        ops=VALUES(ops), updated_at=NOW()
                """, (
                    r.get("season"),
                    r.get("player_name"),
                    r.get("team"),
                    to_float(r.get("avg")),
                    to_int(r.get("games")),
                    to_int(r.get("pa")),
                    to_int(r.get("ab")),
                    to_int(r.get("runs")),
                    to_int(r.get("hits")),
                    to_int(r.get("b2")),
                    to_int(r.get("b3")),
                    to_int(r.get("hr")),
                    to_int(r.get("tb")),
                    to_int(r.get("rbi")),
                    to_int(r.get("sb")),
                    to_int(r.get("cs")),
                    to_int(r.get("bb")),
                    to_int(r.get("hbp")),
                    to_int(r.get("so")),
                    to_int(r.get("gdp")),
                    to_float(r.get("slg")),
                    to_float(r.get("obp")),
                    to_float(r.get("ops")),
                ))
                saved += 1
            except Exception as e:
                print(f"    ⚠️ 타자 저장 실패 ({r.get('player_name')}): {e}")
    conn.commit()
    return saved


# ── training_jobs 테이블에 READY 등록 ─────────────────────────────────────────
def mark_training_ready(conn, pitcher_total: int, hitter_total: int):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO training_jobs (job_name, status, pitcher_rows, hitter_rows, notes)
            VALUES (%s, 'READY', %s, %s, %s)
        """, (
            f"kbo_dl_saju_{datetime.now().strftime('%Y%m%d_%H%M')}",
            pitcher_total,
            hitter_total,
            f"크롤링 완료. 투수 {pitcher_total}건 / 타자 {hitter_total}건 수집 ({START_YEAR}~{END_YEAR})"
        ))
    conn.commit()
    print(f"\n✅ training_jobs에 READY 상태 등록 완료!")
    print(f"   투수 총 {pitcher_total}건 / 타자 총 {hitter_total}건")
    print(f"\n{'='*60}")
    print(f"  ⚠️  이 PC에서의 작업은 여기서 종료합니다.")
    print(f"  GPU PC에서 아래 명령을 실행하여 학습을 시작하세요:")
    print(f"    python deep_learning/saju_train_gpu.py")
    print(f"{'='*60}\n")


# ── 메인 ──────────────────────────────────────────────────────────────────────
def main():
    if not PLAYWRIGHT_OK or not BS4_OK:
        print("❌ 필수 패키지가 없습니다. 위의 안내를 따라 설치하세요.")
        return

    print(f"\n{'='*60}")
    print(f"  🔮 KBO 역대 스탯 크롤러 시작")
    print(f"  수집 기간: {START_YEAR} ~ {END_YEAR}년")
    print(f"  대상: 투수 스탯 + 타자 스탯")
    print(f"{'='*60}\n")

    # DB 연결
    try:
        conn = get_conn()
        init_tables(conn)
    except Exception as e:
        print(f"❌ DB 연결 실패: {e}")
        return

    total_pitchers = 0
    total_hitters  = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        for year in range(START_YEAR, END_YEAR + 1):
            print(f"\n[{year}년] 투수 스탯 수집 중...")
            p_records = crawl_page(browser, PITCHER_URL, year, PITCHER_COLS, "투수")
            if p_records:
                saved = save_pitchers(conn, p_records)
                total_pitchers += saved
                print(f"  💾 투수 {saved}건 DB 저장")

            time.sleep(2)  # 사이트 부하 방지

            print(f"[{year}년] 타자 스탯 수집 중...")
            h_records = crawl_page(browser, HITTER_URL, year, HITTER_COLS, "타자")
            if h_records:
                saved = save_hitters(conn, h_records)
                total_hitters += saved
                print(f"  💾 타자 {saved}건 DB 저장")

            time.sleep(2)

        browser.close()

    # 학습 준비 완료 → READY 등록 후 종료
    mark_training_ready(conn, total_pitchers, total_hitters)
    conn.close()


if __name__ == "__main__":
    main()
