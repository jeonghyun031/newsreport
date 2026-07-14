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

  // 구단별 대표 선발 투수 매핑 사전
  const TEAM_PITCHERS = {
    '삼성': '원태인',
    '한화': '류현진',
    'KIA': '양현종',
    '두산': '곽빈',
    'LG': '임찬규',
    'KT': '고영표',
    'SSG': '김광현',
    '롯데': '반즈',
    'NC': '하트',
    '키움': '후라도'
  };

  // FastAPI 백엔드 주소
  const BACKEND_URL = 'http://localhost:8000';

  // 경기 일정 조회 API 연동 (당일 경기 필터링 및 Fallback)
  const fetchSchedules = async () => {
    setLoadingSchedules(true);
    try {
      const res = await fetch(`${BACKEND_URL}/api/schedule`);
      if (!res.ok) throw new Error('경기 일정 API 응답 실패');
      const data = await res.json();
      
      // 당일 경기 필터링 (로컬 날짜 추출 YYYYMMDD)
      const localToday = new Date();
      const yyyy = localToday.getFullYear();
      const mm = String(localToday.getMonth() + 1).padStart(2, '0');
      const dd = String(localToday.getDate()).padStart(2, '0');
      const todayStr = `${yyyy}${mm}${dd}`; 
      
      let filtered = data.filter(sched => sched.date === todayStr);
      if (filtered.length === 0 && data.length > 0) {
        // 오늘 경기가 없다면, DB 내의 경기 날짜들 중 오늘과 시간상 가장 가까운(절대값 차이가 최소인) 날짜 찾기
        const availableDates = [...new Set(data.map(s => s.date))];
        let closestDate = availableDates[0];
        let minDiff = Infinity;
        
        availableDates.forEach(dateStr => {
          const y = parseInt(dateStr.substring(0, 4), 10);
          const m = parseInt(dateStr.substring(4, 6), 10) - 1;
          const d = parseInt(dateStr.substring(6, 8), 10);
          const targetDateObj = new Date(y, m, d);
          
          const diff = Math.abs(localToday - targetDateObj);
          if (diff < minDiff) {
            minDiff = diff;
            closestDate = dateStr;
          }
        });
        
        filtered = data.filter(sched => sched.date === closestDate);
      }
      
      setSchedules(filtered);
      
      if (filtered.length > 0) {
        setSelectedSchedule(filtered[0]); // 첫 경기 자동 선택
        // 양팀 선발 사주 자동 패치 기동 (상대팀 및 구장 정보 동시 공급)
        const awayPitcher = TEAM_PITCHERS[filtered[0].away_team] || '투수';
        const homePitcher = TEAM_PITCHERS[filtered[0].home_team] || '투수';
        fetchSaju(awayPitcher, filtered[0].home_team, filtered[0].stadium, false);
        fetchSaju(homePitcher, filtered[0].away_team, filtered[0].stadium, true);
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

  // 개별 선발 투수의 Qwen AI 사주풀이 호출 (Lazy loading 및 파라미터 공급)
  const fetchSaju = async (pitcherName, opponentTeam, stadiumName, isHome) => {
    const type = isHome ? 'home' : 'away';
    setLoadingSaju(prev => ({ ...prev, [type]: true }));
    setSajuResult(prev => ({ ...prev, [type]: '' }));
    try {
      const url = `${BACKEND_URL}/api/saju?pitcher=${encodeURIComponent(pitcherName)}&opponent=${encodeURIComponent(opponentTeam)}&stadium=${encodeURIComponent(stadiumName)}`;
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

  // 경기 선택 이벤트 헬퍼
  const handleSelectSchedule = (sched) => {
    setSelectedSchedule(sched);
    const awayPitcher = TEAM_PITCHERS[sched.away_team] || '투수';
    const homePitcher = TEAM_PITCHERS[sched.home_team] || '투수';
    // 양 팀 선발 투수 사주풀이 실시간 로드 (상대팀 및 경기장 공급)
    fetchSaju(awayPitcher, sched.home_team, sched.stadium, false);
    fetchSaju(homePitcher, sched.away_team, sched.stadium, true);
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
      </div>

      {/* 메인 레이아웃 */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 md:p-6 flex flex-col">
        {activeTab === 'news' ? (
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
                    placeholder="KBO 구단명(한화, 삼성, 두산 등) 또는 키워드를 검색하고 Enter를 누르세요..."
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
                  
                  <div className="flex-1 min-h-[220px] bg-slate-50 rounded-lg p-4 overflow-y-auto">
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

                  <div className="flex-1 min-h-[220px] max-h-[260px] bg-slate-50 rounded-lg p-4 text-xs text-slate-600 leading-relaxed overflow-y-auto">
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
                    <span className="font-semibold text-slate-700 bg-slate-100 px-2 py-0.5 rounded">articel_db</span>
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
        ) : (
          /* 신규 사주 및 경기 일정 영역 */
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
                        <div className="text-xs text-sky-300 font-bold mt-1">선발: {TEAM_PITCHERS[selectedSchedule.away_team] || '투수'}</div>
                      </div>
                      <div className="text-2xl md:text-3xl font-black italic text-emerald-400 bg-slate-800/80 px-4 py-1 rounded-lg border border-slate-700">VS</div>
                      <div className="text-center">
                        <div className="text-xl md:text-2xl font-black">{selectedSchedule.home_team}</div>
                        <div className="text-xs text-sky-300 font-bold mt-1">선발: {TEAM_PITCHERS[selectedSchedule.home_team] || '투수'}</div>
                      </div>
                    </div>
                  </div>

                  {/* 양 팀 사주풀이 부적 패널 */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* 원정팀 선발 투수 운세 */}
                    <div className="bg-amber-50/40 border border-amber-300/80 rounded-xl p-5 shadow-sm flex flex-col space-y-4">
                      <div className="flex justify-between items-center pb-2 border-b border-amber-200">
                        <h3 className="font-extrabold text-slate-800 text-sm">
                          [원정] {selectedSchedule.away_team} {TEAM_PITCHERS[selectedSchedule.away_team] || '선발'} 사주
                        </h3>
                        <button
                          onClick={() => fetchSaju(TEAM_PITCHERS[selectedSchedule.away_team] || '투수', selectedSchedule.home_team, selectedSchedule.stadium, false)}
                          className="text-[10px] font-bold text-amber-900 bg-amber-200/60 hover:bg-amber-200 px-2 py-0.5 rounded transition border border-amber-300"
                        >
                          도사님께 점괘 묻기
                        </button>
                      </div>
                      <div className="flex-1 min-h-[400px] max-h-[500px] bg-amber-100/20 rounded-lg p-4 overflow-y-auto leading-relaxed border border-amber-250/50 shadow-inner">
                        {loadingSaju.away ? (
                          <div className="h-full min-h-[350px] flex flex-col justify-center items-center space-y-3">
                            <div className="w-8 h-8 border-4 border-amber-600 border-t-transparent rounded-full animate-spin"></div>
                            <span className="text-xs text-amber-800 font-bold">도사님이 엽전을 던져 운세를 풀고 계시네...</span>
                          </div>
                        ) : sajuResult.away ? (
                          <div className="text-slate-800 font-bold whitespace-pre-wrap text-xs md:text-sm leading-relaxed antialiased">
                            {sajuResult.away}
                          </div>
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
                          [홈] {selectedSchedule.home_team} {TEAM_PITCHERS[selectedSchedule.home_team] || '선발'} 사주
                        </h3>
                        <button
                          onClick={() => fetchSaju(TEAM_PITCHERS[selectedSchedule.home_team] || '투수', selectedSchedule.away_team, selectedSchedule.stadium, true)}
                          className="text-[10px] font-bold text-amber-900 bg-amber-200/60 hover:bg-amber-200 px-2 py-0.5 rounded transition border border-amber-300"
                        >
                          도사님께 점괘 묻기
                        </button>
                      </div>
                      <div className="flex-1 min-h-[400px] max-h-[500px] bg-amber-100/20 rounded-lg p-4 overflow-y-auto leading-relaxed border border-amber-250/50 shadow-inner">
                        {loadingSaju.home ? (
                          <div className="h-full min-h-[350px] flex flex-col justify-center items-center space-y-3">
                            <div className="w-8 h-8 border-4 border-amber-600 border-t-transparent rounded-full animate-spin"></div>
                            <span className="text-xs text-amber-800 font-bold">도사님이 엽전을 던져 운세를 풀고 계시네...</span>
                          </div>
                        ) : sajuResult.home ? (
                          <div className="text-slate-800 font-bold whitespace-pre-wrap text-xs md:text-sm leading-relaxed antialiased">
                            {sajuResult.home}
                          </div>
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
      </main>

      {/* 푸터 */}
      <footer className="bg-slate-900 text-slate-500 py-4 text-center text-xs border-t border-slate-800">
        <p>© 2026 KBO News Briefing System. Built with React, Tailwind CSS and FastAPI.</p>
      </footer>
    </div>
  );
}

export default App;
