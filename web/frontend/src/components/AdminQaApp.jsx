import { useEffect, useRef, useState } from 'react';
import LocalQa from './LocalQa';
import { MODEL_DATA_NOTICE, TRIAL_RECORD_NOTICE } from '../lib/qaPrivacy';
import { clearAdminSession, getAdminSession, loginAdmin, logoutAdmin, setAdminSession } from '../lib/localQaApi';
import '../styles/local-qa.css';
import '../styles/local-qa-auth.css';

const BASE_PATH = import.meta.env.BASE_URL.replace(/\/$/, '');
const LOGIN_PATH = `${BASE_PATH}/login`;
const QA_PATH = `${BASE_PATH}/local-qa`;
const isLoginPage = window.location.pathname.replace(/\/$/, '') === LOGIN_PATH;

function expireSession() {
  clearAdminSession();
  window.location.replace(`${LOGIN_PATH}?expired=1`);
}

function AuthMark() {
  return <span className="lqa-auth-mark" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="m16 4 11 6.4v12.8L16 29 5 22.6V10.4L16 4Z" /><path d="m5 10.4 11 6.4 11-6.4M16 16.8V29M10.5 7.2l11 6.4v12.5" /></svg></span>;
}

function AuthBrand() {
  return <div className="lqa-auth-brand"><AuthMark /><div><strong>CAD INSIGHT</strong><span>建筑图纸问答</span></div><span className="lqa-auth-beta">BETA</span></div>;
}

function AuthIcon({ type }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {type === 'layers' && <><path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5M3 16l9 5 9-5" /></>}
    {type === 'chat' && <><path d="M20 11.5a8 8 0 0 1-8 8 9 9 0 0 1-4-.8L3 20l1.3-5a8 8 0 1 1 15.7-3.5Z" /><path d="M8 10h8M8 14h5" /></>}
    {type === 'target' && <><circle cx="12" cy="12" r="6" /><circle cx="12" cy="12" r="2" /><path d="M12 2v3m0 14v3M2 12h3m14 0h3" /></>}
    {type === 'lock' && <><rect x="6" y="10" width="12" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2" /></>}
    {type === 'arrow' && <path d="M4 12h15m-6-6 6 6-6 6" />}
  </svg>;
}

function ArchitectureIllustration() {
  return <figure className="lqa-auth-illustration">
    <svg viewBox="0 0 580 290" fill="none" aria-hidden="true">
      <defs><pattern id="auth-plan-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0v24" stroke="#dfdcd4" strokeWidth=".65" /></pattern></defs>
      <rect x="0" y="0" width="580" height="290" fill="url(#auth-plan-grid)" />
      <g stroke="#b7bab3" strokeWidth="1"><path d="m115 211 180-104 170 98-180 104-170-98ZM115 211v-27m350 21v-27" transform="translate(0 -40)" /><path d="M84 156 280 43M310 43l190 110" strokeDasharray="3 5" /><path d="m81 151 6 10m190-123 6 10m24-10 6 10m184 100 6 10" /></g>
      <g stroke="#3a413c" strokeWidth="1.5" strokeLinejoin="round">
        <path d="m115 171 180-104 170 98-180 104-170-98Z" fill="#f9f8f4" />
        <path d="M115 171v-55L295 12v55L115 171Z" fill="#ecebe4" />
        <path d="M295 12 465 110v55L295 67V12Z" fill="#fdfcf9" />
        <path d="m115 116 8-5v55l-8 5v-55Z" fill="#d6d9d1" /><path d="m295 12 8 5v55l-8-5V12Z" fill="#d6d9d1" />
        <path d="m205 119 85 49 90-52" /><path d="M290 168v45m-35-65-45 26" />
        <path d="m205 119 0-34 85 49v34l-85-49Z" fill="#e4e7df" /><path d="m290 134 90-52v34l-90 52v-34Z" fill="#f6f6ef" />
        <path d="m290 180 0 33 26 15" /><path d="m205 119-38 22" />
        <path d="m151 118 29-17V74l-29 17v27Z" fill="#d4dcd5" /><path d="m214 82 29-17V38l-29 17v27Z" fill="#d4dcd5" />
        <path d="m325 66 33 19V58l-33-19v27Zm60 35 33 19V93l-33-19v27Z" fill="#f0eee5" />
      </g>
      <g stroke="#e56b43" strokeWidth="1.6" strokeLinejoin="round">
        <path d="m318 175 62-36 56 32-62 36-56-32Z" fill="#f5d7c7" /><path d="m318 175 62-36 56 32-62 36-56-32Z" strokeDasharray="4 3" />
        <path d="m344 163 30 17 31-18M374 180v13" />
        <circle cx="374" cy="174" r="3.5" fill="#e56b43" />
      </g>
      <path d="M438 168h49v-34" stroke="#e56b43" strokeWidth="1" /><circle cx="487" cy="130" r="3" fill="#e56b43" />
      <g fill="#7b8078" fontSize="9" fontFamily="ui-monospace, monospace"><text x="28" y="235">DRAWING → UNDERSTANDING</text><text x="467" y="235">01 / PLAN</text></g>
    </svg>
    <figcaption>建筑轴测示意 · 视觉展示</figcaption>
  </figure>;
}

function AuthLoading({ error = '' }) {
  return <main className="lqa-auth-shell lqa-auth-loading">
    <AuthBrand />
    <div className="lqa-auth-loading-card">
      {!error && <span className="lqa-auth-spinner" aria-hidden="true" />}
      <h1>{error ? '暂时无法连接工作台' : '正在打开你的工作台'}</h1>
      <p role={error ? 'alert' : 'status'}>{error || '正在验证登录，请稍候…'}</p>
      {error && <button className="lqa-auth-submit" onClick={() => window.location.reload()}>重新连接 <AuthIcon type="arrow" /></button>}
    </div>
  </main>;
}

function Login({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const secure = window.location.protocol === 'https:';

  async function submit(event) {
    event.preventDefault();
    if (!secure || busy) return;
    setBusy(true);
    setError('');
    try {
      const session = await loginAdmin(username.trim(), password);
      setPassword('');
      onLogin(session);
      window.location.replace(QA_PATH);
    } catch (failure) {
      setPassword('');
      setError(failure.status === 429 ? '登录尝试过于频繁，请稍后再试。' : failure.message);
    } finally { setBusy(false); }
  }

  return <main className="lqa-auth-shell lqa-auth-page">
    <header className="lqa-auth-header"><AuthBrand /><span className="lqa-auth-header-note">让建筑经验，遇见 AI</span></header>
    <div className="lqa-auth-layout">
      <section className="lqa-auth-story" aria-labelledby="auth-story-title">
        <span className="lqa-auth-eyebrow"><i /> A NEW WAY TO READ DRAWINGS</span>
        <h1 id="auth-story-title">让图纸，<br /><em>开口说话。</em></h1>
        <p className="lqa-auth-description">从一张 CAD，到一个清晰的答案。<br />用自然语言，探索图纸里的文字、图元与几何关系。</p>
        <ArchitectureIllustration />
        <div className="lqa-auth-capabilities">
          <div><AuthIcon type="layers" /><strong>中文图纸预览</strong><span>DXF / DWG</span></div>
          <div><AuthIcon type="chat" /><strong>自然语言问答</strong><span>真实图纸 · 真实回答</span></div>
          <div><AuthIcon type="target" /><strong>回答依据定位</strong><span>从答案回到图纸</span></div>
        </div>
      </section>
      <section className="lqa-auth-login" aria-labelledby="login-title">
        <div className="lqa-auth-card">
          <span className="lqa-auth-card-kicker">YOUR DRAWING WORKSPACE</span>
          <h2 id="login-title">进入图纸工作台</h2>
          <p className="lqa-auth-card-intro">登录后，开始你的下一次图纸探索。</p>
          {new URLSearchParams(window.location.search).has('expired') && <div className="lqa-auth-banner" role="status">登录已过期，请重新登录并上传图纸。</div>}
          {!secure && <div className="lqa-auth-banner lqa-auth-banner--error" role="alert">请通过已配置的 HTTPS 地址访问，以保护登录凭证。</div>}
          {error && <div className="lqa-auth-banner lqa-auth-banner--error" role="alert">{error}</div>}
          <form onSubmit={submit}>
            <div className="lqa-auth-field"><label htmlFor="admin-username">账号</label>
              <input id="admin-username" name="username" autoComplete="username" placeholder="输入分配给你的账号" value={username} onChange={(event) => setUsername(event.target.value)} required maxLength={128} disabled={busy || !secure} /></div>
            <div className="lqa-auth-field"><label htmlFor="admin-password">密码 / 访问 key</label>
              <input id="admin-password" name="password" type="password" autoComplete="current-password" placeholder="输入你的密码或访问 key" value={password} onChange={(event) => setPassword(event.target.value)} required maxLength={512} disabled={busy || !secure} /></div>
            <button className="lqa-auth-submit" type="submit" disabled={busy || !secure || !username.trim() || !password}>{busy ? <><span className="lqa-auth-spinner" aria-hidden="true" /> 正在登录…</> : <>登录工作台 <AuthIcon type="arrow" /></>}</button>
          </form>
          <div className="lqa-auth-security"><AuthIcon type="lock" /><span>{secure ? 'HTTPS 安全连接 · 仅授权账号可访问' : '请使用 HTTPS 安全连接'}</span></div>
        </div>
        <div className="lqa-auth-data-note"><span>关于你的图纸数据与试用记录</span><p>{MODEL_DATA_NOTICE}</p><p>{TRIAL_RECORD_NOTICE}</p></div>
      </section>
    </div>
    <footer className="lqa-auth-footer"><span>BUILT FOR ARCHITECTURAL CURIOSITY</span><span>建筑图纸问答 · 只读探索</span></footer>
  </main>;
}

export default function AdminQaApp() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [loggingOut, setLoggingOut] = useState(false);
  const activeRef = useRef(true);

  function acceptSession(value) {
    setAdminSession(value);
    setSession(value);
  }

  useEffect(() => {
    activeRef.current = true;
    document.title = '建筑图纸问答 · 图纸工作台';
    document.documentElement.lang = 'zh-CN';
    let inProgress = false;
    let hasAuthenticated = false;
    async function checkSession() {
      if (inProgress) return;
      inProgress = true;
      try {
        const value = await getAdminSession();
        if (!activeRef.current) return;
        hasAuthenticated = true;
        acceptSession(value);
        setError('');
        if (isLoginPage) window.location.replace(QA_PATH);
      } catch (failure) {
        if (!activeRef.current) return;
        if (failure.status === 401) {
          setSession(null);
          clearAdminSession();
          if (!isLoginPage) {
            if (hasAuthenticated) expireSession();
            else window.location.replace(LOGIN_PATH);
          }
        } else { setError(failure.message); }
      } finally {
        if (activeRef.current) setLoading(false);
        inProgress = false;
      }
    }
    function onExpired() {
      setSession(null);
      expireSession();
    }
    function onVisibility() { if (document.visibilityState === 'visible') checkSession(); }
    checkSession();
    const timer = window.setInterval(checkSession, 30_000);
    window.addEventListener('cad-session-expired', onExpired);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      activeRef.current = false;
      window.clearInterval(timer);
      window.removeEventListener('cad-session-expired', onExpired);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, []);

  useEffect(() => {
    if (!session?.expires_at) return;
    const raw = session.expires_at;
    const expires = typeof raw === 'number' ? raw * 1000 : Date.parse(raw);
    if (!Number.isFinite(expires)) return;
    const timer = window.setTimeout(() => { setSession(null); expireSession(); }, Math.max(0, expires - Date.now()));
    return () => window.clearTimeout(timer);
  }, [session]);

  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true);
    setError('');
    try {
      await logoutAdmin();
      setSession(null);
      clearAdminSession();
      window.location.replace(LOGIN_PATH);
    } catch (failure) { setError(`退出未完成：${failure.message} 请重试。`); }
    finally { setLoggingOut(false); }
  }

  async function refreshAccount() {
    try {
      const value = await getAdminSession();
      if (activeRef.current) acceptSession(value);
    } catch (failure) {
      if (failure.status === 401) expireSession();
    }
  }

  if (loading) return <AuthLoading />;
  if (isLoginPage && !session) return <Login onLogin={acceptSession} />;
  if (!session) return <AuthLoading error={error} />;
  return <><LocalQa adminUsername={session.username} account={session} onAccountRefresh={refreshAccount} onLogout={logout} loggingOut={loggingOut} />{error && <div className="lqa-auth-error lqa-banner lqa-banner--error" role="alert">{error}</div>}</>;
}
