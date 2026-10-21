import { useEffect, useMemo, useRef, useState } from 'react';
import DxfViewerComponent from './DxfViewerComponent';
import { askLocalDrawing, getLocalDemo, getLocalFont, getLocalPreview, getLocalStatus, uploadLocalDrawing } from '../lib/localQaApi';
import '../styles/local-qa.css';

const STARTER_QUESTIONS = [
  { label: '读懂这张图', description: '梳理图层、房间与建筑对象', question: '请概括这张建筑图纸的内容，列出图层、房间名称和可识别的建筑对象，并说明哪些信息尚不确定。', icon: 'plan' },
  { label: '查找房间与文字', description: '查找名称与标注，追溯原图依据', question: '图纸里有哪些房间名称和面积标注？请列出对应的图层和图元依据。', icon: 'search' },
  { label: '统计门窗图块', description: '按块名或型号分组，了解数量', question: '请统计图纸中的门窗图块，按块名或型号分组，并说明统计依据和局限。', icon: 'grid' },
];

function Icon({ name, size = 20 }) {
  const paths = {
    plan: <><path d="M3 3h18v18H3zM3 10h8V3M11 10v11M11 15h10" /><path d="M6 6h2M15 7h3M15 11h3" /></>,
    upload: <><path d="M12 16V3m-4 4 4-4 4 4M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5" /></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
    grid: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 12h18M12 3v18" /></>,
    arrow: <><path d="M5 12h14m-5-5 5 5-5 5" /></>,
    send: <><path d="m12 4 7 7M12 4l-7 7m7-7v16" /></>,
    target: <><rect x="5" y="5" width="14" height="14" rx="2" /><path d="M12 2v5m0 10v5M2 12h5m10 0h5" /></>,
    file: <><path d="M14 2H5v20h14V7zm0 0v5h5M8 12h8M8 16h6" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    focus: <><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" /></>,
    guide: <><path d="M12 5C9 3 6 3 3 4v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15" /></>,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    shield: <><path d="m12 3 8 3v5c0 5-3 8-8 10-5-2-8-5-8-10V6z" /><path d="m8 12 3 3 5-6" /></>,
    chevron: <path d="m8 10 4 4 4-4" />,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.plan}</svg>;
}

function inlineText(text) {
  return String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
    if (part.startsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`')) return <code key={index}>{part.slice(1, -1)}</code>;
    return part;
  });
}

// Render a small, safe Markdown subset; model output is never inserted as HTML.
function AnswerText({ text }) {
  const lines = String(text || '').split('\n');
  const blocks = [];
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (!line.trim()) continue;
    if (line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] || '')) {
      const cells = (row) => row.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
      const headings = cells(line);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(cells(lines[i]));
        i += 1;
      }
      i -= 1;
      blocks.push(<div className="lqa-answer-table" key={i}><table><thead><tr>{headings.map((cell, index) => <th key={index}>{inlineText(cell)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{row.map((cell, col) => <td key={col}>{inlineText(cell)}</td>)}</tr>)}</tbody></table></div>);
    } else if (/^#{1,6}\s/.test(line)) {
      blocks.push(<h4 key={i}>{inlineText(line.replace(/^#{1,6}\s+/, ''))}</h4>);
    } else if (/^\s*[-*]\s+/.test(line)) {
      blocks.push(<p className="lqa-answer-bullet" key={i}><span aria-hidden="true">•</span><span>{inlineText(line.replace(/^\s*[-*]\s+/, ''))}</span></p>);
    } else {
      blocks.push(<p key={i}>{inlineText(line)}</p>);
    }
  }
  return <div className="lqa-answer-text">{blocks}</div>;
}

function validBounds(evidence) {
  const bounds = evidence.bounds;
  if (bounds && ['minX', 'minY', 'maxX', 'maxY'].every((key) => Number.isFinite(bounds[key]))) return bounds;
  return null;
}

function Evidence({ items, onLocate }) {
  const [expanded, setExpanded] = useState(false);
  if (!items?.length) return null;
  const visible = expanded ? items : items.slice(0, 5);
  return <div className="lqa-evidence">
    <div className="lqa-evidence-heading"><Icon name="target" size={15} /><span>图纸依据 · {items.length} 项</span></div>
    <div className="lqa-evidence-list">{visible.map((item, index) => {
      const bounds = validBounds(item);
      return <div className="lqa-evidence-row" key={`${item.handle || item.entity_handle || 'item'}-${index}`}>
        <div><span className="lqa-evidence-label">{item.label || item.text_excerpt || item.text || item.entity_type || '图元'}</span><span className="lqa-evidence-meta">{item.entity_type || 'ENTITY'} · {item.layer || '未指定图层'} · #{item.handle || item.entity_handle || '—'}</span></div>
        {bounds && <button className="lqa-locate" onClick={() => onLocate(bounds)} title="在图纸中定位" aria-label={`定位 ${item.label || item.handle || '图元'}`}><Icon name="target" size={14} /><span>定位</span></button>}
      </div>;
    })}</div>
    {items.length > 5 && <button className="lqa-text-button" onClick={() => setExpanded(!expanded)}>{expanded ? '收起依据' : `展开其余 ${items.length - 5} 项`}</button>}
  </div>;
}

export default function LocalQa({ adminUsername, onLogout, loggingOut = false }) {
  const [focusMode, setFocusMode] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState('');
  const [modelVerified, setModelVerified] = useState(false);
  const [checking, setChecking] = useState(true);
  const [session, setSession] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadFormat, setUploadFormat] = useState('');
  const [demoLoading, setDemoLoading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [dragging, setDragging] = useState(false);
  const [dxfUrl, setDxfUrl] = useState('');
  const [pngUrl, setPngUrl] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewErrors, setPreviewErrors] = useState({});
  const [previewMode, setPreviewMode] = useState('static');
  const [chineseFontUrl, setChineseFontUrl] = useState('');
  const [fontState, setFontState] = useState('idle');
  const [viewerMissingChars, setViewerMissingChars] = useState(false);
  const [question, setQuestion] = useState('');
  const [messages, setMessages] = useState([]);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState('');
  const inputRef = useRef(null);
  const questionRef = useRef(null);
  const viewerRef = useRef(null);
  const chatEndRef = useRef(null);
  const pendingBoundsRef = useRef(null);
  const fontRequestStartedRef = useRef(false);
  const activeRef = useRef(true);
  const blobUrlsRef = useRef(new Set());
  const latinFontUrl = `${import.meta.env.BASE_URL}fonts/NotoSans-Regular.ttf`;
  const fontUrls = useMemo(() => chineseFontUrl
    ? [latinFontUrl, chineseFontUrl]
    : [latinFontUrl], [chineseFontUrl, latinFontUrl]);
  const fileInfo = session?.file_info || {};
  const convertedDwg = fileInfo.source_format?.toLowerCase() === 'dwg' && fileInfo.normalized_format?.toLowerCase() === 'dxf';

  function createPreviewUrl(blob) {
    if (!activeRef.current) return '';
    const url = URL.createObjectURL(blob);
    blobUrlsRef.current.add(url);
    return url;
  }

  async function refreshStatus() {
    setChecking(true);
    setStatusError('');
    try { setStatus(await getLocalStatus()); } catch (error) { setStatusError(error.message); }
    finally { setChecking(false); }
  }

  useEffect(() => {
    activeRef.current = true;
    const blobUrls = blobUrlsRef.current;
    document.title = '建筑图纸问答 · 只读工作台';
    document.documentElement.lang = 'zh-CN';
    refreshStatus();
    return () => {
      activeRef.current = false;
      for (const url of blobUrls) URL.revokeObjectURL(url);
      blobUrls.clear();
    };
  }, []);
  useEffect(() => {
    if (!focusMode) return;
    const exitOnEscape = (event) => { if (event.key === 'Escape') setFocusMode(false); };
    window.addEventListener('keydown', exitOnEscape);
    return () => window.removeEventListener('keydown', exitOnEscape);
  }, [focusMode]);
  useEffect(() => () => { if (dxfUrl) URL.revokeObjectURL(dxfUrl); }, [dxfUrl]);
  useEffect(() => () => { if (pngUrl) URL.revokeObjectURL(pngUrl); }, [pngUrl]);
  useEffect(() => () => { if (chineseFontUrl) URL.revokeObjectURL(chineseFontUrl); }, [chineseFontUrl]);
  useEffect(() => {
    if (previewMode !== 'interactive' || !session || fontRequestStartedRef.current) return;
    fontRequestStartedRef.current = true;
    setFontState('loading');
    getLocalFont().then((blob) => {
      setChineseFontUrl(createPreviewUrl(blob));
      setFontState('ready');
    }).catch(() => setFontState('unavailable'));
  }, [previewMode, session]);
  useEffect(() => {
    if (messages.length || asking || askError) chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages, asking, askError]);

  async function loadDrawing(file) {
    if (!file || uploading || asking) return;
    if (!/\.(dxf|dwg)$/i.test(file.name)) { setUploadError('请选择 DXF 或 DWG 图纸。PDF 图纸暂不在这个原型的问答范围内。'); return; }
    if (file.size > 25 * 1024 * 1024) { setUploadError('图纸超过 25 MB，请先拆分图纸或清理无用图元。'); return; }
    if (/\.dwg$/i.test(file.name) && status?.dwg_available !== true) { setUploadError(checking ? '正在检查 DWG 转换服务，请稍后重试。' : 'DWG 转换服务暂不可用。请先在 CAD 软件中另存为 DXF 后上传。'); return; }
    setUploadFormat(/\.dwg$/i.test(file.name) ? 'dwg' : 'dxf');
    setUploading(true);
    setUploadError('');
    try {
      const uploaded = await uploadLocalDrawing(file);
      setSession(uploaded);
      setMessages([]);
      setAskError('');
      setQuestion('');
      setDxfUrl('');
      setPngUrl('');
      setPreviewMode('static');
      setPreviewErrors({});
      setViewerMissingChars(false);
      setPreviewLoading(true);
      const [dxf, png] = await Promise.allSettled([getLocalPreview(uploaded.session_id, 'dxf'), getLocalPreview(uploaded.session_id, 'render')]);
      const failures = {};
      if (dxf.status === 'fulfilled') setDxfUrl(createPreviewUrl(dxf.value));
      else failures.interactive = dxf.reason.message;
      if (png.status === 'fulfilled') setPngUrl(createPreviewUrl(png.value));
      else { failures.static = '暂未生成静态预览。可以使用交互图纸查看，问答仍可继续。'; if (dxf.status === 'fulfilled') setPreviewMode('interactive'); }
      setPreviewErrors(failures);
    } catch (error) { setUploadError(error.message); }
    finally { setUploading(false); setPreviewLoading(false); }
  }

  async function loadDemo() {
    if (uploading || asking || demoLoading) return;
    setUploadError('');
    setDemoLoading(true);
    try { await loadDrawing(await getLocalDemo()); }
    catch (error) { setUploadError(error.message); }
    finally { setDemoLoading(false); }
  }

  async function submitQuestion(event) {
    event?.preventDefault();
    const prompt = question.trim();
    if (!prompt || !session || asking || uploading) return;
    const history = messages.filter((message) => !message.failed).map(({ role, content }) => ({ role, content })).slice(-12);
    setMessages((current) => [...current, { role: 'user', content: prompt }]);
    setQuestion('');
    setAsking(true);
    setAskError('');
    try {
      const data = await askLocalDrawing(session.session_id, prompt, history);
      setMessages((current) => [...current, { role: 'assistant', content: data.answer, data }]);
      setModelVerified(true);
      setStatus((current) => ({ ...current, model: data.model || current?.model, configured: true }));
    } catch (error) {
      setAskError(error.message);
      setModelVerified(false);
      setMessages((current) => current.map((message, index) => index === current.length - 1 ? { ...message, failed: true } : message));
      setQuestion(prompt);
    } finally { setAsking(false); }
  }

  function locate(bounds) {
    if (previewMode !== 'interactive') {
      setPreviewMode('interactive');
      pendingBoundsRef.current = bounds;
      return;
    }
    viewerRef.current?.focusOnBounds(bounds);
  }

  const statusText = checking ? '正在检查服务' : statusError ? '服务未连接' : modelVerified ? '模型已连通' : status?.configured ? '模型已配置 · 待验证' : '模型尚未配置';

  return <main className={`lqa-app lqa-workbench ${focusMode ? 'lqa-app--focused' : ''}`}>
    <aside className="lqa-sidebar" aria-label="工作台导航">
      <div className="lqa-brand"><div className="lqa-brand-icon"><Icon name="plan" size={24} /></div><div><span className="lqa-brand-name">图纸知间</span><span className="lqa-brand-en">CAD INSIGHT</span></div></div>
      <div className="lqa-nav-label">工作空间</div>
      <nav className="lqa-navigation">
        <button className={!guideOpen ? 'active' : ''} aria-current={!guideOpen ? 'page' : undefined} onClick={() => { setGuideOpen(false); document.getElementById('lqa-workspace')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}><Icon name="plan" size={19} /><span>图纸问答</span><span className="lqa-nav-indicator" /></button>
        <button className={guideOpen ? 'active' : ''} aria-expanded={guideOpen} aria-controls="lqa-guide" onClick={() => setGuideOpen(!guideOpen)}><Icon name="guide" size={19} /><span>使用指南</span></button>
      </nav>
      <div className="lqa-sidebar-bottom">
        <div className="lqa-sample-card"><span className="lqa-sample-icon"><Icon name="file" size={23} /></span><h3>从一张图纸开始</h3><p>用合成建筑示例，体验从提问到定位的完整过程。</p><button disabled={uploading || asking || demoLoading} onClick={loadDemo}>{demoLoading ? '正在加载…' : '打开合成示例'}<Icon name="arrow" size={16} /></button></div>
        <div className="lqa-sidebar-note"><Icon name="shield" size={16} /><span>只读分析，保留原始图纸</span></div>
        <div className="lqa-sidebar-edition">ARCHITECTURE × AI</div>
      </div>
    </aside>
    <div className="lqa-main">
    <header className="lqa-header">
      <div className="lqa-breadcrumb"><span>工作空间</span><span>/</span><h1>建筑图纸问答</h1></div>
      <div className="lqa-header-actions">
        <button className={`lqa-status ${statusError || (!checking && !status?.configured) ? 'lqa-status--error' : ''}`} onClick={refreshStatus} disabled={checking} title="检查后端配置；模型是否可用将在实际问答后验证"><span className={`lqa-dot ${modelVerified ? 'lqa-dot--verified' : ''}`} /><span>{statusText}</span><Icon name="refresh" size={13} /></button>
        <button data-testid="focus-mode-toggle" aria-label={focusMode ? '退出专注模式' : '开启专注模式'} className={`lqa-focus-button ${focusMode ? 'active' : ''}`} onClick={() => { setFocusMode(!focusMode); setGuideOpen(false); }} aria-pressed={focusMode} title={focusMode ? '退出专注模式（Esc）' : '收起侧栏与介绍，放大图纸工作区'}><Icon name="focus" size={17} /><span>{focusMode ? '退出专注' : '专注模式'}</span></button>
        {onLogout && <div className="lqa-admin"><span className="lqa-avatar" aria-hidden="true">{(adminUsername || 'A').slice(0, 1).toUpperCase()}</span><span className="lqa-admin-name">{adminUsername}</span><button className="lqa-text-button" disabled={loggingOut} onClick={onLogout}>{loggingOut ? '正在退出…' : '退出登录'}</button></div>}
      </div>
    </header>
    <div className="lqa-main-content">
    <section className="lqa-intro"><div><span className="lqa-eyebrow"><span />让专业知识，遇见 AI</span><h2>让图纸里的信息，<em>清晰可见。</em></h2><p>读懂文字与图块，计算几何信息，让每一个回答都有迹可循。</p></div><div className="lqa-steps"><span><b>01</b>上传图纸</span><Icon name="arrow" size={16} /><span><b>02</b>自然提问</span><Icon name="arrow" size={16} /><span><b>03</b>定位依据</span></div></section>
    {guideOpen && <section className="lqa-guide" id="lqa-guide" aria-label="使用指南"><div className="lqa-guide-heading"><h2>三步，开始读懂一张图纸</h2><button className="lqa-icon-button" aria-label="关闭使用指南" onClick={() => setGuideOpen(false)}><Icon name="close" size={18} /></button></div><div className="lqa-guide-grid"><div><span>01 / 上传</span><h3>选择你的 CAD 图纸</h3><p>支持 DXF，服务可用时也支持 DWG 自动转换。最大 25 MB；初次体验可以使用合成建筑示例。</p></div><div><span>02 / 提问</span><h3>从具体的问题开始</h3><p>询问房间文字、图层、图块数量或闭合轮廓面积。明确对象名称，更容易找到相关信息。</p></div><div><span>03 / 核对</span><h3>回到图纸，查看依据</h3><p>点击回答下方的“定位”，在交互图纸中核对图元。几何面积不等同于建筑净面积，复杂图纸需人工复核。</p></div></div><p className="lqa-guide-privacy">图纸在服务器解析；相关结构化图元、文字、属性和对话会发送至 DeepSeek。请使用有权处理的公开或脱敏图纸。</p></section>}

    {statusError && <div className="lqa-banner lqa-banner--error" role="alert">{statusError}</div>}
    {status && !status.configured && <div className="lqa-banner" role="status">后端尚未配置模型密钥。可以先上传并查看图纸，问答需要完成模型配置。</div>}
    <div className="lqa-workspace" id="lqa-workspace">
      <section className="lqa-drawing-panel" aria-label="图纸工作区">
        <div className="lqa-panel-header"><div className="lqa-panel-title"><Icon name={session ? 'file' : 'plan'} size={18} /><span title={fileInfo.filename}>{session ? fileInfo.filename : '图纸工作区'}</span>{session && <span className="lqa-readonly">只读</span>}</div>{session ? <button className="lqa-text-button" disabled={uploading || asking} onClick={() => inputRef.current?.click()}><Icon name="upload" size={15} />更换图纸</button> : <span className="lqa-panel-caption">DXF / DWG</span>}</div>
        <input type="file" ref={inputRef} accept=".dxf,.dwg" hidden aria-label="选择 CAD 图纸" onChange={(event) => { loadDrawing(event.target.files?.[0]); event.target.value = ''; }} />

        {uploadError && <div className="lqa-banner lqa-banner--error" role="alert">{uploadError}</div>}
        {session && uploading && <div className="lqa-banner" role="status">{previewLoading ? '图元已提取，正在生成预览…' : uploadFormat === 'dwg' ? '正在上传并自动转换 DWG 为 DXF，随后解析图元与生成预览…' : '正在上传、解析图元与生成预览…'}</div>}
        {session && !uploading && convertedDwg && <div className="lqa-conversion-notice" data-testid="dwg-conversion-notice" role="status">{fileInfo.conversion_notice || 'DWG 已在服务器自动转换为 DXF；预览与只读问答基于转换结果。'}</div>}
        {!session ? <div className={`lqa-dropzone ${dragging ? 'lqa-dropzone--active' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); loadDrawing(event.dataTransfer.files?.[0]); }}>
          <div className="lqa-plan-illustration" aria-hidden="true"><svg viewBox="0 0 340 220" fill="none"><path className="lqa-plan-dimension" d="M43 26v169M37 26h12M37 195h12M59 210h222M59 204v12M281 204v12M59 12h222M59 6v12M281 6v12" /><path className="lqa-plan-sheet" d="M59 27h222v168H59z" /><path className="lqa-plan-room" d="M159 28h120v93H159z" /><path className="lqa-plan-wall" d="M59 27h222v168H59zM159 27v40m0 33v95M59 115h57m29 0h14M159 121h44m31 0h47M221 121v74" /><path className="lqa-plan-furniture" d="M79 49h59v44H79zM84 54h49v9H84zM83 84h51M79 139h46v35H79zM88 146h28v21H88zM180 55h78v38h-78zM188 49v6m17-6v6m17-6v6m18-6v6M188 93v6m17-6v6m17-6v6m18-6v6M240 139h23v38h-23zM244 148h15m-15 14h15M177 145h26v29h-26z" /><path className="lqa-plan-door" d="M159 68a31 31 0 0 1 31 32h-31M116 115a29 29 0 0 0 29 29v-29M203 121v31a31 31 0 0 0 31-31" /><path className="lqa-plan-window" d="M80 25h52m-52 4h52M183 25h73m-73 4h73M279 47v52m4-52v52M57 137v39m4-39v39" /><circle className="lqa-plan-pin" cx="217" cy="73" r="7" /><path className="lqa-plan-pin-cross" d="M217 62v5m0 12v5m-11-11h5m12 0h5" /></svg><span><Icon name="upload" size={23} /></span><div className="lqa-plan-label">YOUR NEXT INSIGHT STARTS HERE</div></div>
          <h3>{uploading ? uploadFormat === 'dwg' ? '正在转换与解析 DWG…' : '正在解析图纸…' : '你的下一次洞察，从这里开始'}</h3><p>{uploading && uploadFormat === 'dwg' ? '服务器先将 DWG 自动转换为 DXF，再读取图元与生成预览。' : '拖放 CAD 文件到这里，或选择一张图纸'}</p>
          <button className="lqa-button lqa-button--primary" onClick={() => inputRef.current?.click()} disabled={uploading}><Icon name="upload" size={17} />{uploading ? '正在上传与提取' : '上传 CAD 图纸'}</button>
          <span className="lqa-file-hint">{status?.dwg_available ? '支持 DXF / DWG，单文件最大 25 MB' : '支持 DXF，单文件最大 25 MB'}<br />{status?.dwg_available ? 'DWG 自动转换为 DXF；复杂对象请核对预览' : checking ? '正在检查 DWG 自动转换服务…' : 'DWG 转换服务暂不可用，请先另存为 DXF'}</span>
          <div className="lqa-demo-row"><span>还没有图纸？</span><button className="lqa-text-button" disabled={uploading || demoLoading} onClick={loadDemo}>{demoLoading ? '正在加载示例…' : '试用建筑示例'}<Icon name="arrow" size={15} /></button></div>
        </div> : <>
          <div className="lqa-drawing-toolbar"><div className="lqa-view-tabs" aria-label="预览模式"><button aria-pressed={previewMode === 'interactive'} className={previewMode === 'interactive' ? 'active' : ''} onClick={() => setPreviewMode('interactive')}>交互图纸</button><button aria-pressed={previewMode === 'static'} className={previewMode === 'static' ? 'active' : ''} onClick={() => setPreviewMode('static')}>静态预览</button></div><span>{Number(fileInfo.entity_count || 0).toLocaleString()} 图元 <span>·</span> {fileInfo.layer_count || 0} 图层</span></div>
          <div className="lqa-preview">
            {previewLoading ? <div className="lqa-preview-notice"><div className="lqa-spinner" /><span>正在生成图纸预览…</span></div> : previewErrors[previewMode] ? <div className="lqa-preview-notice"><Icon name="file" size={30} /><p>{previewErrors[previewMode]}</p></div> : previewMode === 'interactive' && (fontState === 'idle' || fontState === 'loading') ? <div className="lqa-preview-notice"><div className="lqa-spinner" /><span>正在加载开源中文字体…</span></div> : previewMode === 'interactive' && dxfUrl ? <DxfViewerComponent ref={viewerRef} dxfUrl={dxfUrl} fontUrls={fontUrls} fileEncoding={fileInfo.dxf_encoding || 'utf-8'} locale="zh" onReady={({ hasMissingChars }) => { setViewerMissingChars(hasMissingChars); if (pendingBoundsRef.current) { viewerRef.current?.focusOnBounds(pendingBoundsRef.current); pendingBoundsRef.current = null; } }} /> : pngUrl ? <img src={pngUrl} alt={`${fileInfo.filename} 的原始图纸预览`} /> : <div className="lqa-preview-notice">暂无预览</div>}
          </div>
          <div className="lqa-drawing-footer"><span><span className="lqa-dot lqa-dot--verified" />图纸已解析</span><span>{previewMode === 'interactive' ? (viewerMissingChars || fontState === 'unavailable' ? '部分字符缺少字形，请用静态预览核对' : '滚轮缩放 · 拖动平移 · 点击依据定位') : '点击答案依据，可切换到交互图纸定位'}</span></div>
          <details className="lqa-layers"><summary>查看图层 <span>{fileInfo.layer_count || 0}</span></summary><div>{fileInfo.layers?.map((layer) => <span key={layer}>{layer}</span>)}</div></details>
        </>}
        <div className="lqa-capabilities"><span><Icon name="check" size={14} />提取图纸文字</span><span><Icon name="check" size={14} />检索图层图块</span><span><Icon name="check" size={14} />定位答案依据</span></div>
      </section>

      <section className="lqa-chat-panel" aria-label="图纸问答">
        <div className="lqa-panel-header"><div className="lqa-panel-title"><span className="lqa-assistant-mark">✦</span><span>图纸助手</span><span className="lqa-ai-badge">AI</span></div><span className="lqa-model-name">{status?.model || 'DeepSeek'}</span></div>
        <div className="lqa-chat-content" role="log" aria-live="polite" aria-label="问答记录">
          {!messages.length && <div className="lqa-chat-welcome"><div className="lqa-welcome-heading"><div className="lqa-welcome-mark">✦</div><span>从一个好问题开始</span></div><h3>{session ? '图纸已就绪，想了解什么？' : '把找图纸，变成问图纸。'}</h3><p>{session ? '选择一个问题，或输入你想了解的内容。' : '上传一张图纸，试着用自然语言探索其中的信息。'}</p><div className="lqa-question-cards">{STARTER_QUESTIONS.map((item) => <button key={item.label} disabled={!session || uploading || asking} onClick={() => { setQuestion(item.question); questionRef.current?.focus(); }}><span className="lqa-question-icon"><Icon name={item.icon} size={20} /></span><span className="lqa-question-copy"><strong>{item.label}</strong><small>{item.description}</small></span><Icon name="arrow" size={17} /></button>)}</div><div className="lqa-expectation"><Icon name="target" size={16} /><p>回答基于图纸中的真实信息；点击依据，即可回到图元位置核对。</p></div></div>}

          {messages.map((message, index) => <article className={`lqa-message lqa-message--${message.role}`} key={index}>
            <div className="lqa-message-who">{message.role === 'user' ? <><span className="lqa-message-avatar">你</span>你的问题</> : <><span className="lqa-assistant-mark">✦</span> 图纸助手</>}</div>
            {message.role === 'user' ? <p className="lqa-user-text">{message.content}</p> : <><AnswerText text={message.content} />{message.data?.warnings?.length > 0 && <div className="lqa-answer-warnings"><strong>需要核对</strong>{message.data.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</div>}<Evidence items={message.data?.evidence} onLocate={locate} />{message.data?.tools_used?.length > 0 && <details className="lqa-tools"><summary>查看查询过程 · {message.data.tools_used.length} 次工具调用</summary>{message.data.tools_used.map((tool, i) => <div key={i}><strong>{tool.name}</strong><p>{typeof tool.summary === 'string' ? tool.summary : JSON.stringify(tool.summary)}</p><code>{JSON.stringify(tool.args || {})}</code></div>)}</details>}</>}
          </article>)}
          {asking && <div className="lqa-thinking" role="status"><span className="lqa-spinner" /><div><strong>正在检索图纸并组织回答</strong><p>读取相关图元、查询数据，通常需要几十秒…</p></div></div>}
          {askError && <div className="lqa-banner lqa-banner--error" role="alert"><strong>本次问答未成功</strong><p>{askError}</p><span>问题已保留在输入框，可重新发送。</span></div>}
          <div ref={chatEndRef} />
        </div>
        <form className="lqa-composer" onSubmit={submitQuestion}><label htmlFor="lqa-question" className="sr-only">向图纸提问</label><div className="lqa-input-box"><textarea ref={questionRef} id="lqa-question" placeholder={session ? '例如：这张图有哪些房间名称？' : '请先上传一张图纸…'} value={question} onChange={(event) => setQuestion(event.target.value)} disabled={!session || asking || uploading} rows={3} maxLength={4000} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submitQuestion(); } }} /><div className="lqa-input-footer"><span>Enter 发送 · Shift + Enter 换行</span><button type="submit" aria-label="发送问题" disabled={!session || !question.trim() || asking || uploading || status?.configured === false}><Icon name="send" size={20} /></button></div></div><p>相关结构化图元、文字、属性和对话将发送到 DeepSeek；请结合原图复核。</p></form>
      </section>
    </div>
    <footer className="lqa-page-footer"><span>图纸知间 <span className="lqa-footer-divider">/</span> 基于 cad-ai-agent</span><span>几何结果不等于建筑净面积 · 关键结论请结合原图复核</span></footer>
    </div>
    </div>
  </main>;
}
