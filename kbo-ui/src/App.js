import React, { useState, useEffect } from 'react';

function App() {
  const [news, setNews] = useState([]);
  const [search, setSearch] = useState('');
  const [loadingNews, setLoadingNews] = useState(false);
  const [summary, setSummary] = useState('');
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [selectedArticle, setSelectedArticle] = useState(null);

  // KBO 경기일정 및 투수 사주 대결 상태 변수
  const [activeTab, setActiveTab] = useState('news');
  const [schedules, setSchedules] = useState([]);
  const [loadingSchedules, setLoadingSchedules] = useState(false);
  const [selectedSchedule, setSelectedSchedule] = useState(null);
  const [sajuResult, setSajuResult] = useState({ home: '', away: '' });
  const [loadingSaju, setLoadingSaju] = useState({ home: false, away: false });

  // 관심 구단 뉴스 브리핑 이메일 상태 변수
  const KBO_TEAMS = ['KIA', '삼성', 'LG', '두산', 'SSG', 'KT', '한화', '롯데', 'NC', '키움'];
  const [selectedTeams, setSelectedTeams] = useState(['삼성', 'KIA']);
  const [emailInput, setEmailInput] = useState('');
  const [appPasswordInput, setAppPasswordInput] = useState('');
  const [sendingEmail, setSendingEmail] = useState(false);
  const [subscribing, setSubscribing] = useState(false);
  const [emailResult, setEmailResult] = useState(null);
  const [toastMessage, setToastMessage] = useState('');

  // FastAPI 백엔드 주소
  const BACKEND_URL = 'http://localhost:8000';

  // 경기 일정 조회 API 연동 (당일 경기 필터링 및 Fallback)
  const fetchSchedules = async () => {
    setLoadingSchedules(true);
    try {
      const res = await fetch(`${BACKEND_URL}/api/schedule`);
      if (!res.ok) throw new Error('경기 일정 API 응답 실패');
      const data = await res.json();

      if (!data || data.length === 0) {
        setSchedules([]);
        setSelectedSchedule(null);
        setLoadingSchedules(false);
        return;
      }

      // 날짜 정제 헬퍼 (2026.7.23 또는 2026-07-23 -> 20260723)
      const normalizeDate = (dStr) => {
        if (!dStr) return '';
        const matches = String(dStr).match(/(\d{4})[^\d]*(\d{1,2})[^\d]*(\d{1,2})/);
        if (matches) {
          const y = matches[1];
          const m = matches[2].padStart(2, '0');
          const d = matches[3].padStart(2, '0');
          return `${y}${m}${d}`;
        }
        return String(dStr).replace(/[^0-9]/g, '');
      };

      const localToday = new Date();
      const yyyy = localToday.getFullYear();
      const mm = String(localToday.getMonth() + 1).padStart(2, '0');
      const dd = String(localToday.getDate()).padStart(2, '0');
      const todayStr = `${yyyy}${mm}${dd}`;

      let filtered = data.filter(sched => normalizeDate(sched.date) === todayStr);

      if (filtered.length === 0 && data.length > 0) {
        // 오늘 경기가 없으면 가장 가까운 날짜 경기 찾기
        const availableDates = [...new Set(data.map(s => s.date))];
        let closestDate = availableDates[0];
        let minDiff = Infinity;

        availableDates.forEach(dateStr => {
          const norm = normalizeDate(dateStr);
          if (norm.length >= 8) {
            const y = parseInt(norm.substring(0, 4), 10);
            const m = parseInt(norm.substring(4, 6), 10) - 1;
            const d = parseInt(norm.substring(6, 8), 10);
            const targetDateObj = new Date(y, m, d);
            const diff = Math.abs(localToday - targetDateObj);
            if (diff < minDiff) {
              minDiff = diff;
              closestDate = dateStr;
            }
          }
        });

        filtered = data.filter(sched => sched.date === closestDate);
        if (filtered.length === 0) filtered = data.slice(0, 10);
      }

      setSchedules(filtered);

      if (filtered.length > 0) {
        setSelectedSchedule(filtered[0]);
        setSajuResult({ home: '', away: '' });
      } else {
        setSelectedSchedule(null);
      }
    } catch (err) {
      console.error(err);
      alert('경기 일정 데이터를 가져오는 데 실패했습니다.');
    } finally {
      setLoadingSchedules(false);
    }
  };

  // 개별 선발 투수의 Qwen AI 사주풀이 호출 (Lazy loading 및 파라미터 공급, 선발 투수 미정 시 호출 전면 차단)
  const fetchSaju = async (pitcherName, opponentTeam, stadiumName, isHome, gameDate, myTeam) => {
    const type = isHome ? 'home' : 'away';
    if (!pitcherName || pitcherName.trim() === '' || pitcherName === '미정') {
      setSajuResult(prev => ({ ...prev, [type]: '선발 투수가 지정되지 않아 도사님도 운세를 점치실 수 없네.' }));
      return;
    }
    setLoadingSaju(prev => ({ ...prev, [type]: true }));
    setSajuResult(prev => ({ ...prev, [type]: '' }));
    try {
      const url = `${BACKEND_URL}/api/saju?pitcher=${encodeURIComponent(pitcherName)}&opponent=${encodeURIComponent(opponentTeam)}&stadium=${encodeURIComponent(stadiumName)}&date=${encodeURIComponent(gameDate || '')}&my_team=${encodeURIComponent(myTeam || '')}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error('사주 API 응답 실패');
      const data = await res.json();
      setSajuResult(prev => ({ ...prev, [type]: data.saju }));
    } catch (err) {
      console.error(err);
      setSajuResult(prev => ({ ...prev, [type]: '도사님이 마운드 기도를 하러 가셨는지 점괘를 낼 수 없구나. 다시 흔들어보시게.' }));
    } finally {
      setLoadingSaju(prev => ({ ...prev, [type]: false }));
    }
  };

  // 경기 선택 이벤트 헬퍼 (자동 호출 제거, 사용자가 버튼 클릭 시에만 사주 로드)
  const handleSelectSchedule = (sched) => {
    setSelectedSchedule(sched);
    setSajuResult({ home: '', away: '' });
  };

  // 뉴스 목록 로드 & 백그라운드 자동 요약 연동
  const fetchNews = async (queryVal = '') => {
    setLoadingNews(true);
    setLoadingSummary(true);
    setSummary('');
    try {
      const url = queryVal
        ? `${BACKEND_URL}/api/news?query=${encodeURIComponent(queryVal)}`
        : `${BACKEND_URL}/api/news`;
      const res = await fetch(url);
      if (!res.ok) throw new Error('API 응답 실패');
      const data = await res.json();
      setNews(data);

      // 기사 목록 표를 화면에 즉시 렌더링하도록 목록 스피너 먼저 종료! (체감 0초 렌더링)
      setLoadingNews(false);

      if (data.length > 0) {
        // 첫 번째 기사 디폴트 선택 및 본문 Lazy 로드 기동
        fetchSelectedArticleContent(data[0]);
        // ⚡ AI 3줄 요약은 await 하지 않고 백그라운드 비동기 쓰레드로 즉시 쏩니다.
        runAutomaticSummary(queryVal);
      } else {
        setSelectedArticle(null);
        setSummary('요약할 뉴스 기사가 없습니다.');
        setLoadingSummary(false);
      }
    } catch (err) {
      console.error(err);
      alert('DB 뉴스 기사를 가져오는 데 실패했습니다.');
      setLoadingNews(false);
      setLoadingSummary(false);
    }
  };

  // 백엔드 Qwen 3대 키워드 브리핑 자동 호출 (가벼운 query만 백엔드로 전송)
  const runAutomaticSummary = async (queryVal) => {
    try {
      const res = await fetch(`${BACKEND_URL}/api/summarize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: queryVal })
      });
      if (!res.ok) throw new Error('요약 API 실패');
      const data = await res.json();
      setSummary(data.summary);
    } catch (err) {
      console.error(err);
      setSummary('요약을 가져오는 데 실패했습니다. 백엔드 서버 상태를 확인해 주세요.');
    } finally {
      setLoadingSummary(false);
    }
  };

  // 특정 기사의 본문을 실시간으로 개별 로드 (Lazy Loading)
  const fetchSelectedArticleContent = async (item) => {
    // 이미 본문이 로드되어 캐싱되어 있다면 API 중복 호출 방지
    if (item.content) {
      setSelectedArticle(item);
      return;
    }

    // 본문 로드 시작
    try {
      setSelectedArticle({ ...item, content: '본문을 불러오는 중입니다...' });
      const res = await fetch(`${BACKEND_URL}/api/news/content?title=${encodeURIComponent(item.title)}`);
      if (!res.ok) throw new Error('본문 로드 실패');
      const data = await res.json();

      const updatedArticle = { ...item, content: data.content };
      setSelectedArticle(updatedArticle);

      // 목록 데이터에도 본문을 캐싱하여 재클릭 시 고속 렌더링
      setNews(prevNews => prevNews.map(n => n.title === item.title ? updatedArticle : n));
    } catch (err) {
      console.error(err);
      setSelectedArticle({ ...item, content: '본문을 불러오는 데 실패했습니다. 다시 시도해 주세요.' });
    }
  };

  // 관심 구단 토글 헬퍼
  const toggleTeamSelect = (team) => {
    setSelectedTeams(prev =>
      prev.includes(team)
        ? prev.filter(t => t !== team)
        : [...prev, team]
    );
  };

  // 실시간 이메일 브리핑 전송
  const handleSendEmailBriefing = async () => {
    if (!emailInput || !emailInput.includes('@')) {
      alert('유효한 이메일 주소를 입력해 주세요.');
      return;
    }
    if (selectedTeams.length === 0) {
      alert('최소 하나 이상의 관심 구단을 선택해 주세요.');
      return;
    }

    setSendingEmail(true);
    setEmailResult(null);
    try {
      const res = await fetch(`${BACKEND_URL}/api/send-email-briefing`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: emailInput,
          teams: selectedTeams,
          app_password: appPasswordInput
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '이메일 발송 실패');

      setEmailResult(data);
      setToastMessage(`🎉 ${emailInput} 주소로 이메일 브리핑을 발송했습니다!`);
      setTimeout(() => setToastMessage(''), 5000);
    } catch (err) {
      console.error(err);
      alert(`이메일 발송 오류: ${err.message}`);
    } finally {
      setSendingEmail(false);
    }
  };

  // 매일 브리핑 구독 등록
  const handleSubscribeNewsletter = async () => {
    if (!emailInput || !emailInput.includes('@')) {
      alert('유효한 이메일 주소를 입력해 주세요.');
      return;
    }
    if (selectedTeams.length === 0) {
      alert('최소 하나 이상의 관심 구단을 선택해 주세요.');
      return;
    }
    if (!appPasswordInput || appPasswordInput.trim().length < 8) {
      alert('정기 구독 최초 등록을 위해 Gmail 16자리 앱 비밀번호를 입력해 주세요.\n(구글 계정 -> 보안 -> 2단계 인증 -> 앱 비밀번호)');
      return;
    }

    setSubscribing(true);
    try {
      const res = await fetch(`${BACKEND_URL}/api/subscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: emailInput,
          teams: selectedTeams,
          app_password: appPasswordInput
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '구독 등록 실패');

      setToastMessage(data.message);
      setTimeout(() => setToastMessage(''), 5000);
    } catch (err) {
      console.error(err);
      alert(`구독 등록 오류: ${err.message}`);
    } finally {
      setSubscribing(false);
    }
  };

  // 탭 변경 감지 및 데이터 패치 자동화
  useEffect(() => {
    if (activeTab === 'news') {
      fetchNews();
    } else if (activeTab === 'saju') {
      fetchSchedules();
    }
  }, [activeTab]);

  // 검색 입력 처리 (엔터 키)
  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      fetchNews(search);
    }
  };

  // 브리핑 가독성을 극대화하기 위한 가독성 렌더러 (이모지 배제)
  const renderSummaryContent = (summaryText) => {
    if (!summaryText) return null;

    // 줄 단위 쪼개기
    const lines = summaryText.split('\n').map(l => l.trim()).filter(Boolean);

    return (
      <div className="space-y-4">
        {lines.map((line, idx) => {
          // 불릿 기호 제거
          let cleanLine = line.replace(/^[\s\-\*]+/, '').trim();

          // **[키워드]** 본문 패턴 매칭
          const match = cleanLine.match(/^\*?\*?\[(.*?)\]\*?\*?\s*(.*)$/);

          if (match) {
            const keyword = match[1];
            const description = match[2];
            return (
              <div key={idx} className="p-4 bg-white border border-slate-200 rounded-lg shadow-sm hover:border-emerald-300 hover:shadow-md transition duration-200">
                <span className="inline-block text-xs font-black text-emerald-800 bg-emerald-100/80 border border-emerald-200 px-3 py-1 rounded-md mb-2 uppercase tracking-wide">
                  {keyword}
                </span>
                <p className="text-sm md:text-base font-bold text-slate-800 leading-relaxed">
                  {description}
                </p>
              </div>
            );
          }

          // 패턴 매칭 실패 시 일반 기사 텍스트 굵고 선명하게 렌더링
          return (
            <div key={idx} className="p-4 bg-white border border-slate-200 rounded-lg shadow-sm">
              <p className="text-sm md:text-base font-bold text-slate-800 leading-relaxed">
                {cleanLine}
              </p>
            </div>
          );
        })}
      </div>
    );
  };

  // 사주 결과 텍스트의 #, ##, **, * 마크다운 기호를 깔끔한 서식 및 하이라이트로 다듬어 렌더링하는 전용 렌더러
  const renderSajuContent = (rawText) => {
    if (!rawText) return null;

    // **단어** 하이라이트 렌더링 헬퍼
    const formatBoldText = (textStr) => {
      if (!textStr) return '';
      const parts = textStr.split(/(\*\*.*?\*\*)/g);
      return parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          const boldVal = part.slice(2, -2);
          return (
            <span key={i} className="font-extrabold text-amber-950 bg-amber-200/60 px-1 py-0.5 rounded mx-0.5 border border-amber-300/40">
              {boldVal}
            </span>
          );
        }
        return part;
      });
    };

    const lines = rawText.split('\n');

    return (
      <div className="space-y-2 text-slate-800 text-xs md:text-sm antialiased">
        {lines.map((line, idx) => {
          let trimmed = line.trim();
          if (!trimmed) return <div key={idx} className="h-1" />;

          // # 메인 제목 (# 박준영 오늘의 야구 사주풀이)
          if (trimmed.startsWith('# ')) {
            const titleText = trimmed.replace(/^#\s*/, '').replace(/\*\*/g, '');
            return (
              <h3 key={idx} className="text-base md:text-lg font-black text-amber-950 border-b-2 border-amber-400/80 pb-2 mb-3 mt-1">
                {titleText}
              </h3>
            );
          }

          // ## 소제목 (## 1. 👁️ 오늘 선발의 운세 총평 등)
          if (trimmed.startsWith('## ')) {
            const subTitleText = trimmed.replace(/^##\s*/, '').replace(/\*\*/g, '');
            return (
              <h4 key={idx} className="text-sm md:text-base font-extrabold text-amber-900 border-b border-amber-300/60 pb-1 mt-4 mb-2 flex items-center">
                {subTitleText}
              </h4>
            );
          }

          // * 또는 - 불릿 리스트 항목
          if (trimmed.startsWith('* ') || trimmed.startsWith('- ')) {
            const bulletContent = trimmed.replace(/^[\*\-]\s*/, '');
            return (
              <div key={idx} className="flex items-start space-x-2 pl-1 my-1">
                <span className="text-amber-700 font-bold text-sm leading-tight">•</span>
                <p className="flex-1 font-medium text-slate-800 leading-relaxed">
                  {formatBoldText(bulletContent)}
                </p>
              </div>
            );
          }

          // 일반 본문 문장
          return (
            <p key={idx} className="font-medium text-slate-800 leading-relaxed my-1">
              {formatBoldText(trimmed)}
            </p>
          );
        })}
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800 font-sans flex flex-col">
      {/* KBO 테마 헤더 (이모지 전면 삭제) */}
      <header className="bg-gradient-to-r from-slate-900 via-sky-950 to-slate-900 text-white py-6 px-8 shadow-md border-b-4 border-emerald-500 flex justify-between items-center">
        <div>
          <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight">KBO Daily News Alarm</h1>
          <p className="text-xs text-sky-400 font-medium">AWS RDS MySQL and Qwen 3.5 AI Integration</p>
        </div>
        <div className="text-sm font-semibold px-3 py-1 bg-slate-800 rounded-full border border-slate-700 text-emerald-400 flex items-center space-x-1.5">
          <span className="w-2.5 h-2.5 bg-emerald-500 rounded-full animate-ping"></span>
          <span>FastAPI Live</span>
        </div>
      </header>

      {/* 글로벌 탭 메뉴 (이모지 삭제) */}
      <div className="bg-slate-900 border-b border-slate-800 px-8 flex space-x-6">
        <button
          onClick={() => setActiveTab('news')}
          className={`py-3.5 px-4 text-xs md:text-sm font-bold border-b-2 transition ${activeTab === 'news' ? 'border-emerald-500 text-emerald-400' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
        >
          KBO 실시간 뉴스 & AI 브리핑
        </button>
        <button
          onClick={() => setActiveTab('saju')}
          className={`py-3.5 px-4 text-xs md:text-sm font-bold border-b-2 transition ${activeTab === 'saju' ? 'border-emerald-500 text-emerald-400' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
        >
          KBO 경기 일정 & 투수 사주풀이
        </button>
        <button
          onClick={() => setActiveTab('email')}
          className={`py-3.5 px-4 text-xs md:text-sm font-bold border-b-2 transition ${activeTab === 'email' ? 'border-emerald-500 text-emerald-400' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
        >
          ✉️ AI 이메일 브리핑 구독
        </button>
      </div>

      {/* Toast 토스트 알림 메시지 바 */}
      {toastMessage && (
        <div className="bg-emerald-500 text-slate-950 text-center py-2 px-4 font-bold text-xs md:text-sm shadow-md transition animate-bounce">
          {toastMessage}
        </div>
      )}

      {/* 메인 레이아웃 */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 md:p-6 flex flex-col">
        {activeTab === 'news' && (
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 w-full">
            {/* 좌측 메인 영역 */}
            <section className="lg:col-span-3 flex flex-col space-y-6">
              {/* 검색 바 */}
              <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-100 flex items-center space-x-4">
                <span className="text-lg font-bold text-slate-700 whitespace-nowrap">검색 필터</span>
                <div className="relative flex-1">
                  <input
                    type="text"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="KBO 구단명(한화, 삼성, 롯데 등) 또는 키워드를 검색하고 Enter를 누르세요..."
                    className="w-full pl-4 pr-12 py-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:bg-white text-sm transition-all"
                  />
                  <button
                    onClick={() => fetchNews(search)}
                    className="absolute right-2 top-1.5 px-3 py-1 bg-sky-950 text-white rounded text-xs font-semibold hover:bg-emerald-600 transition"
                  >
                    검색
                  </button>
                </div>
              </div>

              {/* KBO DB 뉴스 테이블 목록 (이모지 삭제) */}
              <div className="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden flex flex-col">
                <div className="px-5 py-4 bg-slate-900 text-white flex justify-between items-center">
                  <h2 className="font-bold text-base">
                    KBO DB 기사 목록 ({news.length}건)
                  </h2>
                  <span className="text-[10px] px-2.5 py-1 bg-emerald-500 text-slate-950 font-extrabold rounded-md uppercase tracking-wider">
                    {loadingSummary ? 'AI 분석 중...' : '자동 요약 완료'}
                  </span>
                </div>

                <div className="overflow-x-auto max-h-[300px] divide-y divide-slate-100">
                  {loadingNews ? (
                    <div className="py-12 text-center text-slate-400 text-sm">기사를 로드하는 중...</div>
                  ) : news.length > 0 ? (
                    <table className="w-full text-left border-collapse text-sm">
                      <thead className="bg-slate-50 sticky top-0 text-slate-500 font-semibold uppercase text-xs border-b border-slate-100">
                        <tr>
                          <th className="px-5 py-3">날짜</th>
                          <th className="px-5 py-3">언론사</th>
                          <th className="px-5 py-3">제목</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {news.map((item, idx) => (
                          <tr
                            key={idx}
                            onClick={() => fetchSelectedArticleContent(item)}
                            className={`hover:bg-slate-50 cursor-pointer transition ${selectedArticle?.title === item.title ? 'bg-sky-50/50' : ''}`}
                          >
                            <td className="px-5 py-3.5 whitespace-nowrap text-xs text-slate-500">
                              {item.date ? new Date(item.date).toLocaleString('ko-KR', { hour12: false }).substring(0, 16) : 'N/A'}
                            </td>
                            <td className="px-5 py-3.5 whitespace-nowrap font-bold text-slate-700">{item.press}</td>
                            <td className="px-5 py-3.5 text-slate-900 font-bold max-w-md truncate">{item.title}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="py-12 text-center text-slate-400 text-sm">검색 조건에 맞는 기사가 없습니다.</div>
                  )}
                </div>
              </div>

              {/* 기사 요약 및 상세 보기 영역 */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Qwen 3.5 AI 3줄 요약 결과 */}
                <div className="bg-white rounded-xl p-5 shadow-sm border border-slate-100 flex flex-col space-y-4">
                  <div className="flex justify-between items-center pb-2 border-b border-slate-100">
                    <h3 className="font-bold text-slate-800">
                      Qwen 3.5 AI 3줄 브리핑
                    </h3>
                    <span className="text-[10px] px-2 py-0.5 bg-sky-100 text-sky-800 font-semibold rounded-full uppercase tracking-wider">Fast and Accurate</span>
                  </div>

                  <div className="h-auto bg-slate-50 rounded-lg p-4">
                    {loadingSummary ? (
                      <div className="h-full flex flex-col justify-center items-center space-y-2">
                        <div className="w-8 h-8 border-4 border-emerald-500 border-t-transparent rounded-full animate-spin"></div>
                        <span className="text-xs text-slate-400 font-medium">Qwen AI가 DB 팩트 기반 신속 요약 중...</span>
                      </div>
                    ) : summary ? (
                      renderSummaryContent(summary)
                    ) : (
                      <div className="h-full flex justify-center items-center text-slate-400 text-xs">
                        KBO 뉴스 로드 시 자동으로 3대 키워드 브리핑이 생성됩니다.
                      </div>
                    )}
                  </div>
                </div>

                {/* 기사 본문 상세 뷰어 */}
                <div className="bg-white rounded-xl p-5 shadow-sm border border-slate-100 flex flex-col space-y-4">
                  <div className="flex justify-between items-center pb-2 border-b border-slate-100">
                    <h3 className="font-bold text-slate-800">
                      선택된 기사 본문
                    </h3>
                    <div className="flex items-center space-x-2">
                      {selectedArticle?.url && (
                        <a
                          href={selectedArticle.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs font-bold text-sky-600 hover:text-emerald-600 underline"
                        >
                          기사 원문 보기
                        </a>
                      )}
                      {selectedArticle && (
                        <span className="text-xs text-emerald-600 font-bold">
                          {selectedArticle.url ? ` | ${selectedArticle.press}` : selectedArticle.press}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="h-[430px] bg-slate-50 rounded-lg p-4 text-xs text-slate-600 leading-relaxed overflow-y-auto">
                    {selectedArticle ? (
                      <div>
                        <h4 className="font-bold text-slate-850 text-sm mb-2">{selectedArticle.title}</h4>
                        <p className="whitespace-pre-wrap font-semibold">{selectedArticle.content}</p>
                      </div>
                    ) : (
                      <div className="h-full flex justify-center items-center text-slate-400">
                        목록에서 기사를 클릭하면 본문 전체를 확인하실 수 있습니다.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </section>

            {/* 우측 사이드바 (이모지 삭제) */}
            <section className="flex flex-col space-y-6">
              {/* DB 및 API 연결 현황 카드 */}
              <div className="bg-white p-5 rounded-xl shadow-sm border border-slate-100 space-y-4">
                <h3 className="font-bold text-sm text-slate-800 border-b border-slate-100 pb-2">
                  시스템 연결 정보
                </h3>
                <div className="space-y-3 text-xs">
                  <div className="flex justify-between">
                    <span className="text-slate-400">Target Database:</span>
                    <span className="font-semibold text-slate-700 bg-slate-100 px-2 py-0.5 rounded">total_db</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Table Name:</span>
                    <span className="font-semibold text-slate-700 bg-slate-100 px-2 py-0.5 rounded">news_articles</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">LLM Engine:</span>
                    <span className="font-semibold text-slate-700 bg-slate-100 px-2 py-0.5 rounded">Qwen 3.5 35B</span>
                  </div>
                </div>
              </div>

              {/* 많이 본 KBO 뉴스 위젯 (DB 실시간 뉴스 및 실제 수집 URL 동적 연동) */}
              <div className="bg-white p-5 rounded-xl shadow-sm border border-slate-100 space-y-4">
                <h3 className="font-bold text-sm text-slate-800 border-b border-slate-100 pb-2">
                  많이 본 KBO 뉴스
                </h3>
                <ul className="space-y-3">
                  {news.length > 0 ? (
                    news.slice(0, 5).map((item, i) => (
                      <li key={i}>
                        {item.url ? (
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="block p-3 bg-slate-50 hover:bg-emerald-50 rounded border border-slate-100 hover:border-emerald-200 transition text-xs font-semibold text-slate-700 hover:text-slate-900 cursor-pointer shadow-sm hover:shadow"
                          >
                            {`${i + 1}. ${item.title}`}
                          </a>
                        ) : (
                          <div className="block p-3 bg-slate-50 rounded border border-slate-100 text-xs font-semibold text-slate-400">
                            {`${i + 1}. ${item.title} (원문 없음)`}
                          </div>
                        )}
                      </li>
                    ))
                  ) : (
                    <div className="text-slate-400 text-xs py-4 text-center">기사를 불러오는 중입니다...</div>
                  )}
                </ul>
              </div>
            </section>
          </div>
        )}

        {/* 신규 사주 및 경기 일정 영역 */}
        {activeTab === 'saju' && (
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 w-full">
            {/* 좌측: KBO 경기 일정 목록 */}
            <div className="bg-white p-5 rounded-xl shadow-sm border border-slate-100 flex flex-col space-y-4 lg:col-span-1">
              <h2 className="font-extrabold text-sm text-slate-800 border-b border-slate-100 pb-2">
                7월 KBO 경기 일정
              </h2>
              <div className="overflow-y-auto max-h-[600px] space-y-2 pr-1">
                {loadingSchedules ? (
                  <div className="text-center py-12 text-slate-400 text-xs font-semibold">경기 일정을 불러오는 중...</div>
                ) : schedules.length > 0 ? (
                  schedules.map((sched, idx) => (
                    <div
                      key={idx}
                      onClick={() => handleSelectSchedule(sched)}
                      className={`p-3 rounded border text-xs font-bold cursor-pointer transition ${selectedSchedule?.date === sched.date && selectedSchedule?.time === sched.time ? 'border-emerald-500 bg-emerald-50/40 text-emerald-800' : 'border-slate-100 bg-slate-50 hover:bg-slate-100 text-slate-700'}`}
                    >
                      <div className="flex justify-between text-[10px] text-slate-400 mb-1">
                        <span>{sched.date.replace(/(\d{4})(\d{2})(\d{2})/, '$1.$2.$3')} {sched.time}</span>
                        <span>{sched.stadium}</span>
                      </div>
                      <div className="flex justify-between items-center text-xs md:text-sm">
                        <span className="font-black text-slate-800">{sched.away_team}</span>
                        <span className="text-slate-400 text-[9px] px-1.5 py-0.5 bg-slate-200/60 rounded font-extrabold">VS</span>
                        <span className="font-black text-slate-800">{sched.home_team}</span>
                      </div>
                      <div className="mt-1 text-[10px] text-slate-500 text-right">{sched.status}</div>
                    </div>
                  ))
                ) : (
                  <div className="text-center py-12 text-slate-400 text-xs">경기 일정 데이터가 존재하지 않습니다.</div>
                )}
              </div>
            </div>

            {/* 우측: 선발 투수 사주 대결 */}
            <div className="lg:col-span-3 flex flex-col space-y-6">
              {selectedSchedule ? (
                <div className="space-y-6">
                  {/* VERSUS 대결 타이틀 카드 */}
                  <div className="bg-gradient-to-r from-slate-900 via-sky-950 to-slate-900 rounded-xl p-5 text-white shadow border-b-4 border-emerald-500 text-center">
                    <span className="text-[10px] text-sky-400 font-extrabold uppercase tracking-widest">{selectedSchedule.stadium} MATCHUP</span>
                    <div className="flex justify-center items-center space-x-12 mt-3">
                      <div className="text-center">
                        <div className="text-xl md:text-2xl font-black">{selectedSchedule.away_team}</div>
                        <div className="text-xs text-sky-300 font-bold mt-1">선발: {selectedSchedule.away_pitcher || '미정'}</div>
                      </div>
                      <div className="text-2xl md:text-3xl font-black italic text-emerald-400 bg-slate-800/80 px-4 py-1 rounded-lg border border-slate-700">VS</div>
                      <div className="text-center">
                        <div className="text-xl md:text-2xl font-black">{selectedSchedule.home_team}</div>
                        <div className="text-xs text-sky-300 font-bold mt-1">선발: {selectedSchedule.home_pitcher || '미정'}</div>
                      </div>
                    </div>
                  </div>

                  {/* 양 팀 사주풀이 부적 패널 */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* 원정팀 선발 투수 운세 */}
                    <div className="bg-amber-50/40 border border-amber-300/80 rounded-xl p-5 shadow-sm flex flex-col space-y-4">
                      <div className="flex justify-between items-center pb-2 border-b border-amber-200">
                        <h3 className="font-extrabold text-slate-800 text-sm">
                          [원정] {selectedSchedule.away_team} {selectedSchedule.away_pitcher || '미정'} 사주
                        </h3>
                        <button
                          disabled={!selectedSchedule.away_pitcher || selectedSchedule.away_pitcher === '미정'}
                          onClick={() => fetchSaju(selectedSchedule.away_pitcher, selectedSchedule.home_team, selectedSchedule.stadium, false, selectedSchedule.date, selectedSchedule.away_team)}
                          className={`text-[10px] font-bold px-2.5 py-0.5 rounded transition border ${(!selectedSchedule.away_pitcher || selectedSchedule.away_pitcher === '미정') ? 'bg-slate-200 text-slate-400 border-slate-300 cursor-not-allowed' : 'text-amber-900 bg-amber-200/60 hover:bg-amber-200 border-amber-300'}`}
                        >
                          {(!selectedSchedule.away_pitcher || selectedSchedule.away_pitcher === '미정') ? '선발 미정' : '도사님께 점괘 묻기'}
                        </button>
                      </div>
                      <div className="flex-1 min-h-[400px] max-h-[500px] bg-amber-100/20 rounded-lg p-4 overflow-y-auto leading-relaxed border border-amber-250/50 shadow-inner">
                        {loadingSaju.away ? (
                          <div className="h-full min-h-[350px] flex flex-col justify-center items-center space-y-3">
                            <div className="w-8 h-8 border-4 border-amber-600 border-t-transparent rounded-full animate-spin"></div>
                            <span className="text-xs text-amber-800 font-bold">도사님이 엽전을 던져 운세를 풀고 계시네...</span>
                          </div>
                        ) : sajuResult.away ? (
                          renderSajuContent(sajuResult.away)
                        ) : (
                          <div className="h-full min-h-[350px] flex justify-center items-center text-amber-800/60 text-xs text-center font-bold">
                            위의 버튼을 눌러 선발 투수의 오늘 사주 운세를 점쳐 보시게.
                          </div>
                        )}
                      </div>
                    </div>

                    {/* 홈팀 선발 투수 운세 */}
                    <div className="bg-amber-50/40 border border-amber-300/80 rounded-xl p-5 shadow-sm flex flex-col space-y-4">
                      <div className="flex justify-between items-center pb-2 border-b border-amber-200">
                        <h3 className="font-extrabold text-slate-800 text-sm">
                          [홈] {selectedSchedule.home_team} {selectedSchedule.home_pitcher || '미정'} 사주
                        </h3>
                        <button
                          disabled={!selectedSchedule.home_pitcher || selectedSchedule.home_pitcher === '미정'}
                          onClick={() => fetchSaju(selectedSchedule.home_pitcher, selectedSchedule.away_team, selectedSchedule.stadium, true, selectedSchedule.date, selectedSchedule.home_team)}
                          className={`text-[10px] font-bold px-2.5 py-0.5 rounded transition border ${(!selectedSchedule.home_pitcher || selectedSchedule.home_pitcher === '미정') ? 'bg-slate-200 text-slate-400 border-slate-300 cursor-not-allowed' : 'text-amber-900 bg-amber-200/60 hover:bg-amber-200 border-amber-300'}`}
                        >
                          {(!selectedSchedule.home_pitcher || selectedSchedule.home_pitcher === '미정') ? '선발 미정' : '도사님께 점괘 묻기'}
                        </button>
                      </div>
                      <div className="flex-1 min-h-[400px] max-h-[500px] bg-amber-100/20 rounded-lg p-4 overflow-y-auto leading-relaxed border border-amber-250/50 shadow-inner">
                        {loadingSaju.home ? (
                          <div className="h-full min-h-[350px] flex flex-col justify-center items-center space-y-3">
                            <div className="w-8 h-8 border-4 border-amber-600 border-t-transparent rounded-full animate-spin"></div>
                            <span className="text-xs text-amber-800 font-bold">도사님이 엽전을 던져 운세를 풀고 계시네...</span>
                          </div>
                        ) : sajuResult.home ? (
                          renderSajuContent(sajuResult.home)
                        ) : (
                          <div className="h-full min-h-[350px] flex justify-center items-center text-amber-800/60 text-xs text-center font-bold">
                            위의 버튼을 눌러 선발 투수의 오늘 사주 운세를 점쳐 보시게.
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="py-24 text-center text-slate-400 text-sm bg-white rounded-xl border border-slate-100">
                  KBO 일정을 선택하면 선발 투수 사주 대결을 볼 수 있습니다.
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'email' && (
          <div className="max-w-4xl mx-auto w-full flex flex-col space-y-6">
            {/* 상단 안내 카드 */}
            <div className="bg-white rounded-xl p-6 shadow-sm border border-slate-100 space-y-5">
              <div className="flex items-center space-x-3 pb-4 border-b border-slate-100">
                <div className="w-10 h-10 bg-sky-100 rounded-full flex justify-center items-center text-sky-700 font-bold text-xl">✉️</div>
                <div>
                  <h2 className="text-xl font-bold text-slate-800">KBO AI 관심 구단 맞춤 이메일 브리핑</h2>
                  <p className="text-xs text-slate-500">응원하는 구단을 선택하면 Qwen AI가 생성한 3줄 핵심 요약과 최신 뉴스를 발송해 드립니다.</p>
                </div>
              </div>

              {/* 1. 관심 구단 선택 칩 */}
              <div className="space-y-2">
                <label className="text-sm font-bold text-slate-700 block">
                  1. 관심 구단 선택 (복수 선택 가능)
                </label>
                <div className="flex flex-wrap gap-2.5 pt-1">
                  {KBO_TEAMS.map(team => {
                    const isSelected = selectedTeams.includes(team);
                    return (
                      <button
                        key={team}
                        type="button"
                        onClick={() => toggleTeamSelect(team)}
                        className={`px-4 py-2 rounded-lg text-xs font-bold border transition duration-150 flex items-center space-x-1.5 ${isSelected
                          ? 'bg-emerald-500 text-slate-950 border-emerald-400 shadow-sm scale-105'
                          : 'bg-slate-50 text-slate-600 border-slate-200 hover:bg-slate-100'
                          }`}
                      >
                        <span>{isSelected ? '✓' : '+'}</span>
                        <span>{team}</span>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* 2. 수신 이메일 주소 및 3. Gmail 앱 비밀번호 폼 */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-3">
                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-slate-700 block">
                    2. 수신 이메일 주소
                  </label>
                  <input
                    type="email"
                    value={emailInput}
                    onChange={(e) => setEmailInput(e.target.value)}
                    placeholder="example@email.com"
                    className="w-full px-4 py-2.5 border border-slate-200 rounded-lg text-sm focus:outline-none focus:border-emerald-500 shadow-sm"
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-slate-700 block">
                    3. Gmail 앱 비밀번호 (16자리)
                  </label>
                  <input
                    type="password"
                    value={appPasswordInput}
                    onChange={(e) => setAppPasswordInput(e.target.value)}
                    placeholder="16자리 앱 비밀번호 (예: abcdefghijklmnop)"
                    className="w-full px-4 py-2.5 border border-amber-300 bg-amber-50/20 rounded-lg text-sm focus:outline-none focus:border-emerald-500 shadow-sm font-semibold"
                  />
                </div>
              </div>

              {/* 발송 & 정기 구독 등록 버튼 */}
              <div className="flex flex-col sm:flex-row justify-between items-center gap-3 pt-2">
                <div className="text-[11px] text-slate-600 font-medium bg-slate-50 p-2 rounded-lg border border-slate-200">
                  💡 <b>보안 및 구독 안내:</b> 구글 계정 ➔ 보안 ➔ 2단계 인증 ➔ <b>앱 비밀번호(16자리)</b>를 등록하시면 해당 이메일/비밀번호가 DB(`user_info`)에 안전하게 기록되어 정기 브리핑이 발송됩니다.
                </div>
                <div className="flex space-x-2 w-full sm:w-auto justify-end">
                  <button
                    onClick={handleSendEmailBriefing}
                    disabled={sendingEmail}
                    className="px-5 py-2.5 bg-sky-950 text-white rounded-lg text-sm font-bold hover:bg-sky-900 transition disabled:opacity-50 flex items-center space-x-2 whitespace-nowrap shadow-sm"
                  >
                    {sendingEmail ? (
                      <>
                        <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                        <span>생성 & 발송 중...</span>
                      </>
                    ) : (
                      <span>✉️ 지금 브리핑 받기</span>
                    )}
                  </button>
                  <button
                    onClick={handleSubscribeNewsletter}
                    disabled={subscribing}
                    className="px-5 py-2.5 bg-emerald-500 text-slate-950 rounded-lg text-sm font-extrabold hover:bg-emerald-400 transition disabled:opacity-50 flex items-center space-x-2 whitespace-nowrap shadow-sm"
                  >
                    {subscribing ? (
                      <span>등록 중...</span>
                    ) : (
                      <span>🔔 정기 구독 등록</span>
                    )}
                  </button>
                </div>
              </div>
            </div>

            {/* 발송 결과 및 미리보기 카드 */}
            {emailResult && (
              <div className="bg-white rounded-xl p-6 shadow-sm border border-emerald-200 space-y-4 animate-fade-in">
                <div className="flex justify-between items-center pb-3 border-b border-emerald-100">
                  <h3 className="font-bold text-emerald-800 flex items-center space-x-2">
                    <span>✅ 이메일 전송 완료!</span>
                    <span className="text-xs font-normal text-slate-500">({emailResult.email})</span>
                  </h3>
                  <span className="text-xs px-2.5 py-1 bg-emerald-100 text-emerald-800 font-bold rounded-md">
                    선택 구단: {emailResult.teams.join(', ')}
                  </span>
                </div>

                <div className="bg-slate-50 p-4 rounded-lg border border-slate-200 space-y-2">
                  <h4 className="text-xs font-bold text-sky-800 uppercase tracking-wider">🤖 전송된 Qwen AI 브리핑 내용 미리보기</h4>
                  <div className="text-xs md:text-sm text-slate-700 whitespace-pre-wrap leading-relaxed antialiased">
                    {emailResult.preview_summary}
                  </div>
                </div>

                <p className="text-xs text-emerald-700 font-semibold bg-emerald-50 p-3 rounded-lg border border-emerald-100">
                  💡 {emailResult.message}
                </p>
              </div>
            )}
          </div>
        )}
      </main>

      {/* 푸터 */}
      <footer className="bg-slate-900 text-slate-500 py-4 text-center text-xs border-t border-slate-800">
        <p>© 2026 KBO News Briefing System. Built with React, Tailwind CSS and FastAPI.</p>
      </footer>
    </div>
  );
}

export default App;
