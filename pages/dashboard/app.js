// Native AstrBot bridge; management reads never retrieve music or invoke a model.
const $ = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const PAGE_SIZE = 25, CARD_PAGE_SIZE = 6;
let offset = 0, total = 0, loading = false, pendingClear = null;
let configSnapshot = null, configDirty = false, configBusy = false, debugMode = false;
let activeTab = 'overview', detailEpoch = 0, lastRefreshClick = -Infinity;
let lyricRows = [], contextRows = [], historyData = {items:[]};
const pages = {contexts:0, history:0};
// Pinned Lucide static assets; source and ISC/Feather MIT notices in assets/icons.
const iconFiles = {
  music:'music.svg', grid:'dashboard.svg', lyrics:'lyrics.svg',
  layers:'context.svg', message:'reply.svg', settings:'settings.svg',
  refresh:'refresh.svg', search:'search.svg',
};
function node(tag, text, cls) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = String(text);
  if (cls) element.className = cls;
  return element;
}
function icon(name) {
  const glyph = node('span',undefined,'icon');
  const file = Object.hasOwn(iconFiles,name) ? iconFiles[name] : iconFiles.music;
  glyph.style.setProperty('--mc-icon-url',`url("./assets/icons/${file}")`);
  glyph.setAttribute('aria-hidden','true');
  return glyph;
}
document.querySelectorAll('[data-icon]').forEach(el=>el.append(icon(el.dataset.icon)));
function time(value) { return value ? new Date(value*1000).toLocaleString('zh-CN',{hour12:false}) : '暂无'; }
function processedTime(value) { return value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : '暂无'; }
function badge(text, tone='') { return node('span',text,`badge ${tone}`); }
function status(text, error=false) {
  $('status').textContent=text; $('status').dataset.error=String(error); $('retry').hidden=!error||!bridge;
}
function cover(large=false) {
  const placeholder=node('span',undefined,`cover-placeholder ${large?'large':''}`);
  placeholder.setAttribute('role','img'); placeholder.setAttribute('aria-label','暂无专辑封面');
  placeholder.title='当前数据源未提供封面'; placeholder.append(icon('music')); return placeholder;
}
function songHeading(title,artist,large=false) {
  const heading=node('div',undefined,'song-heading'), text=node('div');
  const name=node('h3',title);name.title=title;
  text.append(name,node('p',artist,'muted'));
  heading.append(cover(large),text); return heading;
}
function empty(title,description,kind='music') {
  const el=node('div',undefined,'empty-state');
  el.append(icon(kind),node('h3',title),node('p',description,'muted')); return el;
}
function fields(title,values,layer=null) {
  const section=node('section',undefined,'detail-section');
  if(layer)section.append(node('p',layer,'layer-label'));
  section.append(node('h3',title));
  const dl=node('dl');
  for(const [label,value] of values) dl.append(node('dt',label),node('dd',Array.isArray(value)?value.join('、')||'暂无':value??'暂无'));
  section.append(dl); return section;
}
function details(title,content) {
  $('detail-title').textContent=title;
  $('detail-content').replaceChildren(typeof content==='string'?node('p',content):content);
  if(!$('detail').open)$('detail').showModal();
}
function disclosure(label,getText,debug=false) {
  const el=node('details'); el.append(node('summary',label));
  el.addEventListener('toggle',()=>{
    if(!el.open) { while(el.children.length>1)el.lastChild.remove(); return; }
    if(el.children.length>1)return;
    const text=getText(), pre=node('pre',text);
    if(debug) {
      const tools=node('div',undefined,'debug-tools'), search=node('input'), result=node('p','','muted fine');
      search.type='search'; search.placeholder='搜索文本'; search.setAttribute('aria-label',`搜索 ${label}`);
      const copy=action('复制',async()=>{
        try {await navigator.clipboard.writeText(text);result.textContent='已复制完整文本。';}
        catch {result.textContent='浏览器不允许复制；请选中文本后复制。';}
      });
      const find=()=>{
        const query=search.value.toLocaleLowerCase();
        const lines=query?text.split('\n').filter(line=>line.toLocaleLowerCase().includes(query)):null;
        pre.textContent=lines?lines.join('\n'):text;
        result.textContent=lines?`${lines.length} 行匹配；复制始终包含完整文本。`:'';
      };
      search.addEventListener('input',find); tools.append(search,copy); el.append(tools,result);
    }
    el.append(pre);
  });
  return el;
}
function debugSections(data,keys=null) {
  const container=node('section',undefined,'debug-section');
  if(!debugMode||!data)return container;
  container.append(node('h3','Debug 数据'),node('p','展开后加载文本；搜索按行匹配，复制保留完整内容。','muted fine'));
  const names={raw_lyrics:'Raw Lyrics',raw_translation:'Raw Translation',raw_lyrics_json:'Raw Lyrics JSON',raw_provider_response:'Provider Response',full_music_context:'Full Context JSON',processor_input:'Processor Input',processor_output:'Processor Output',llm_inject_payload:'LLM Inject Payload'};
  for(const [key,label] of Object.entries(names)) {
    if(keys&&!keys.includes(key))continue;
    if(['raw_translation','raw_lyrics_json'].includes(key)&&!(key in data))continue;
    container.append(disclosure(label,()=>data[key]==null?'未捕获；请开启 Debug 后产生新请求。':typeof data[key]==='string'?data[key]:JSON.stringify(data[key],null,2),true));
  }
  if(data.provider_response_status)container.append(node('p',data.provider_response_status==='captured'?'Provider 响应已捕获':'缓存命中或 Provider 未提供响应快照。','muted'));
  if(data.raw_display_truncated||data.raw_lyrics_json?.display_truncated)container.append(node('p','原始歌词展示已达 64000 字符上限；缓存原文保持完整。','muted'));
  return container;
}
const processorStates={success:'处理成功',cache_hit:'复用已生成 Context，跳过小模型',disabled:'未启用',failed:'处理失败，使用原始数据',busy:'处理器忙，使用原始数据',budget_fallback:'超过长度限制，使用原始数据'};
function contextOrigin(retrieval) {
  return {generated_cache:'复用已生成 Context',generated:'本次小模型生成',cache:'复用事实 Context 缓存',built:'本次构建',unknown:'未记录'}[retrieval?.context]||'未记录';
}
function lyricsOrigin(retrieval) {
  return {cache:'来自缓存歌词',context_cache:'来自缓存歌词 · 复用缓存 Context',provider:'本次 Provider 获取歌词',unavailable:'本次未取得歌词',unknown:'歌词读取来源未记录'}[retrieval?.lyrics]||'歌词读取来源未记录';
}
function cacheInputSource(source) {
  return {link:'链接',card:'卡片',unknown:'未知'}[source]||'未知';
}
function contextContent(value) {
  if(!value)return empty('Context 已不可用','记录已离开配置的历史保留范围；回复中的歌曲关联仍可查看。');
  const content=node('div'), info=value.basic_info, analysis=value.analysis, processing=value.processing;
  content.append(songHeading(info.title,info.artist,true));
  content.append(fields('音乐来源数据',[['歌曲',info.title],['歌手',info.artist],['专辑',info.album],['来源',info.source],['本次歌词读取',lyricsOrigin(value.retrieval)],['Context 获取',contextOrigin(value.retrieval)]],'01 / SOURCE LAYER'));
  content.append(fields('AI 辅助分析',[['处理状态',processorStates[processing.status]||processing.status],['使用模型',processing.model||'未使用'],['处理时间',processedTime(processing.processed_at)],['摘要',analysis.song_summary],['情绪',analysis.emotion],['风格',analysis.style],['主题',analysis.theme],['关键词',analysis.keywords],['歌手信息',analysis.artist_info],['格式校验',value.quality.validation==='schema_valid'?'通过':'未评估'],['置信度',value.quality.confidence??'未知（尚未校准）'],['依据',value.quality.evidence==='lyrics_text'?'歌词文本':value.quality.evidence==='metadata_only'?'仅元数据':'原始音乐数据']],'02 / PROCESSING LAYER'));
  content.append(node('p','AI 分析是歌曲文本的辅助解释，未经语义验证；不代表用户情绪或意图。','muted fine'));
  content.append(fields('LLM 上下文使用',[['当前注入开关',value.injection_enabled?'开启':'关闭'],['此条注入状态',value.injection?.status==='injected'?'已追加到 LLM 请求':'已观察；未捕获注入诊断'],['此条注入长度',value.injection?.length!=null?`${value.injection.length} 字符`:'未捕获'],['记录时间',time(value.generated_at)]],'03 / CONTEXT LAYER'));
  content.append(node('p','注入状态表示请求已追加临时数据；不证明模型采纳或最终客户端送达。','muted fine'));
  content.append(debugSections(value.debug)); return content;
}
function lyricContent(value) {
  const content=node('div');
  content.append(fields('歌词摘要',[['来源',value.source],['歌词状态',lyricState(value.lyrics_status)],['翻译状态',value.translation_status==='available'?'有翻译':'无翻译'],['歌词行数',value.line_count],['歌词字符数',value.character_count]],'SOURCE LAYER'));
  const full=value.preview_mode==='full';
  content.append(node('p',full?'当前展示清洗后的全部歌词；缓存原文和 LLM 长度限制不受此设置影响。':'当前预览最多 400 字符，仅限制页面展示；缓存保留原文。可在「配置与处理器 → 缓存设置」开启「歌词缓存预览展示全部歌词」。','muted fine'));
  if(value.preview_truncated)content.append(node('p','歌词较长，当前预览已截取；其余歌词仍在缓存中。','muted fine'));
  content.append(disclosure(full?'歌词预览 · 全部歌词':'歌词预览 · 最多 400 字符',()=>value.preview||'暂无可展示的歌词'));
  content.append(debugSections(value.debug)); return content;
}
function lyricState(value) {return {available:'有歌词',instrumental:'纯音乐',unavailable:'暂无歌词'}[value]||'未知';}
function applyDebug(enabled) {
  if(debugMode!==enabled){ detailEpoch++; if($('detail').open)$('detail').close(); $('detail-content').replaceChildren(); }
  debugMode=enabled; $('debug-toggle').checked=enabled; $('processor-debug-panel').hidden=!enabled;
  if(!enabled)$('processor-debug').replaceChildren();
}
function action(label,handler) {
  const button=node('button',label);
  button.addEventListener('click',async event=>{
    if(button.disabled||event.detail>1)return;
    button.disabled=true; button.setAttribute('aria-busy','true'); button.textContent='处理中…';
    try {await handler();} catch {status('操作失败，请重试此操作或刷新数据。',true);}
    finally {button.disabled=false;button.removeAttribute('aria-busy');button.textContent=label;}
  }); return button;
}
function providerContent(providers) {
  const el=node('div');
  for(const p of providers) {
    const row=node('div',undefined,'provider-row');
    row.append(node('strong',p.name),badge('已注册','success'),node('span',`优先级 ${p.priority} · ${p.platforms.join(' / ')}`,'muted fine'));el.append(row);
  }
  if(!providers.length)el.append(node('p','暂无已注册 Provider','muted'));return el;
}
function overview(data) {
  applyDebug(data.settings.debug_mode);
  $('context-history-note').textContent=`最多 ${data.settings.max_history_items} 条 · ${data.history_persistent?'本地历史':'当前运行'}${data.settings.history_enable?'':' · 停止新增'}`;
  $('version').textContent=`v${data.version}`;
  $('runtime-badge').replaceWith(Object.assign(badge(data.status==='running'?'运行中':'已停止',data.status==='running'?'success':'warning'),{id:'runtime-badge'}));
  $('metrics').replaceChildren();
  const metrics=[['插件状态',data.status==='running'?'运行中':'已停止','当前实例状态','music'],['近期 Context',data.context_count,`最近成功注入 · 最多 ${data.settings.max_history_items} 条`,'layers'],['有效缓存',data.cache_count,`歌曲 ${data.cache_counts.tracks} · 歌词 ${data.cache_counts.lyrics} · Context ${data.cache_counts.music_context}`,'lyrics'],['已注册 Provider',data.providers.length,'注册状态 · 未检测远端连通','grid']];
  for(const [label,value,note,kind] of metrics) {
    const card=node('article',undefined,'metric');card.append(icon(kind),node('span',label,'metric-label'),node('strong',value),node('p',note,'fine'));$('metrics').append(card);
  }
  $('pipeline').replaceChildren();
  for(const [title,note,state,kind,on] of [
    ['来源数据','Provider · 歌曲与歌词',`${data.providers.length} 个已注册`,'music',data.providers.length>0],
    ['AI 辅助分析','Context Processor · 歌曲文本',data.settings.enable_context_processor?'已启用':'未启用','layers',data.settings.enable_context_processor],
    ['LLM 上下文','临时注入 · 保留原始对话',data.settings.enable_context_injection?'注入已启用':'注入已关闭','message',data.settings.enable_context_injection]]) {
    const row=node('div',undefined,'pipeline-step'), glyph=node('span',undefined,'step-icon'), text=node('div');
    glyph.append(icon(kind));text.append(node('strong',title),node('p',note,'step-description'));row.append(glyph,text,badge(state,on?'success':''));$('pipeline').append(row);
  }
  $('providers').replaceChildren(providerContent(data.providers));$('config-providers').replaceChildren(providerContent(data.providers));
  const values=[['缓存状态',data.settings.enable_cache?'启用':'关闭'],['有效时间',`${data.settings.cache_ttl.toLocaleString()} 秒`],['Context 上限',`${data.settings.max_context_length.toLocaleString()} 字符`],['当前处理模型',data.settings.processor_model||'未选择'],['Processor',data.settings.enable_context_processor?'已启用':'关闭（使用原始数据）']];
  if(!data.cache_available)values.push(['缓存存储','不可用']);
  $('settings').replaceChildren(...values.flatMap(([label,value])=>[node('dt',label),node('dd',value)]));
  const observation=fields('当前观察设置',[['历史保存',data.settings.history_enable?'开启':'关闭'],['回复记录',data.settings.history_enable&&data.settings.store_llm_history?`开启 · ${data.history_count} 条`:'停止新增'],['Debug',data.settings.debug_mode?'开启':'关闭'],['保留范围',data.history_persistent?'本地保存 · 重启后保留':'存储不可用 · 当前运行记录']]);
  observation.className='';$('observation').replaceChildren(observation);
}
function lyrics(data) {total=data.total;lyricRows=data.items;renderLyrics();}
function matches(query,values) {return values.some(value=>String(value??'').toLocaleLowerCase().includes(query.toLocaleLowerCase()));}
function renderLyrics() {
  const query=$('lyrics-search').value.trim(), rows=lyricRows.filter(row=>matches(query,[row.title,row.artist,row.source,cacheInputSource(row.input_source)]));
  $('lyrics-rows').replaceChildren();
  if(rows.length){const head=node('div',undefined,'lyric-row list-head');for(const name of ['歌曲 / 艺术家','平台 / 输入','缓存时间','状态','详情'])head.append(node('span',name));$('lyrics-rows').append(head);}
  for(const row of rows) {
    const el=node('article',undefined,'lyric-row'), source=node('div',row.source,'row-source'), date=node('div',time(row.cache_time),'row-time'), state=node('div',undefined,'row-status');
    const origin=badge(`来源：${cacheInputSource(row.input_source)}`);
    origin.title='实际写入歌词缓存时的输入类型；缓存命中不更改，旧条目显示未知。';
    source.append(origin);
    state.append(badge(lyricState(row.lyrics_status),row.lyrics_status==='available'?'success':''));
    el.append(songHeading(row.title,row.artist),source,date,state,action('查看详情',async()=>{
      const epoch=detailEpoch, value=await bridge.apiGet('lyrics/detail',{source:row.source,track_id:row.track_id});
      if(epoch!==detailEpoch)return;
      details(`${row.title} · 歌词`,value?lyricContent(value):'缓存已到期或已清空。');
    }));$('lyrics-rows').append(el);
  }
  if(!rows.length)$('lyrics-rows').append(empty(query?'没有匹配结果':'暂无歌词缓存',query?'尝试其他关键词；搜索仅限当前页。':'分享音乐并完成一次请求后，在这里查看来源数据。','lyrics'));
  $('lyrics-count').textContent=`共 ${total} 条${query?` · 本页匹配 ${rows.length} 条`:''}`;
  $('page-count').textContent=`${total?offset+1:0}–${Math.min(offset+lyricRows.length,total)} / ${total}`;
  $('previous').disabled=offset===0||loading;$('next').disabled=offset+PAGE_SIZE>=total||loading;
}
function pageRows(kind,rows,render) {
  pages[kind]=Math.min(pages[kind],Math.max(0,Math.ceil(rows.length/CARD_PAGE_SIZE)-1));
  const start=pages[kind]*CARD_PAGE_SIZE, pagination=$(kind+'-pagination');
  pagination.replaceChildren();
  if(rows.length>CARD_PAGE_SIZE){
    const previous=action('上一页',async()=>{pages[kind]--;render();}), next=action('下一页',async()=>{pages[kind]++;render();});
    previous.disabled=start===0;next.disabled=start+CARD_PAGE_SIZE>=rows.length;
    pagination.append(previous,node('span',`${start+1}–${Math.min(start+CARD_PAGE_SIZE,rows.length)} / ${rows.length}`),next);
  }
  return rows.slice(start,start+CARD_PAGE_SIZE);
}
function contexts(rows) {contextRows=rows;renderContexts();}
function renderContexts() {
  const query=$('contexts-search').value.trim(), rows=contextRows.filter(row=>matches(query,[row.song_info.title,row.song_info.artist,row.summary]));
  $('context-rows').replaceChildren();
  for(const row of pageRows('contexts',rows,renderContexts)) {
    const card=node('article',undefined,'card');
    card.append(songHeading(row.song_info.title,row.song_info.artist),badge(`${row.metadata.source} · 来源数据`),badge(lyricsOrigin(row.retrieval)),badge(contextOrigin(row.retrieval)),node('p',row.summary_source==='processor'?'AI 摘要':'来源摘要','reply-label'),node('p',row.summary,'clamp'),node('p',`${time(row.generated_at)} · ${row.metadata.has_lyrics?'有歌词':'暂无歌词'}`,'muted fine'));
    card.append(action('查看分层 Context',async()=>{const epoch=detailEpoch,value=await bridge.apiGet(`contexts/${row.id}`);if(epoch===detailEpoch)details('Music Context',contextContent(value));}));$('context-rows').append(card);
  }
  if(!rows.length)$('context-rows').append(empty(query?'没有匹配结果':'尚无 Music Context',query?'尝试其他歌曲、歌手或摘要关键词。':'完成一次音乐上下文注入后，记录会显示在这里。','layers'));
}
function history(data) {historyData=data;renderHistory();}
function renderHistory() {
  const data=historyData, query=$('history-search').value.trim(), rows=data.items.filter(row=>matches(query,[row.context.track.title,row.reply]));
  $('history-note').textContent=`${data.enabled?`保留最近 ${data.limit} 条音乐请求回复`:'已停止新增回复，已有历史保留'}；${data.persistent?'本地保存，重启后可查看':'存储不可用，仅当前运行可查看'}。模型回复按回调原文完整保存，展开可查看全文；用户消息脱敏后最多 2000 字符。此记录不证明客户端送达。`;
  $('history-rows').replaceChildren();
  for(const row of pageRows('history',rows,renderHistory)) {
    const card=node('article',undefined,'card');
    card.append(songHeading(row.context.track.title,row.context.track.artist),badge(lyricsOrigin(row.retrieval)),badge(contextOrigin(row.retrieval)),node('p',time(row.time),'muted fine'),node('p','用户消息','reply-label'),node('p',row.user_message||row.input_summary,'muted'),node('p','模型回复','reply-label'),node('p',row.reply.slice(0,180)+(row.reply.length>180?'…':''),'reply-preview'));
    card.append(disclosure('展开完整回复',()=>row.reply));
    card.append(action('关联 Context · 插件处理信息',async()=>{
      const epoch=detailEpoch,value=await bridge.apiGet(`contexts/${row.id}`);if(epoch!==detailEpoch)return;
      const content=node('div');content.append(fields('用户消息',[['消息',row.user_message||row.input_summary]]),fields('模型回复',[['时间',time(row.time)],['模型',row.model_name||'未提供'],['关联 Context',row.id]]),disclosure('完整回复',()=>row.reply),contextContent(value));details('音乐请求回复',content);
    }));$('history-rows').append(card);
  }
  if(!rows.length)$('history-rows').append(empty(query?'没有匹配结果':data.enabled?'尚无音乐请求回复':'回复记录已关闭',query?'尝试其他歌曲或回复关键词。':data.enabled?'音乐请求完成后，可在这里查看关联回复。':'在配置中启用后，仅保存新请求的近期记录。','message'));
}
async function refresh() {
  if(loading||!bridge)return;
  const requestedTab=activeTab;
  loading=true;$('refresh').disabled=true;$('refresh').replaceChildren(icon('refresh'),node('span','刷新中…'));$('refresh').setAttribute('aria-busy','true');
  syncConfigAvailability();
  $(requestedTab).setAttribute('aria-busy','true');status('正在读取运行数据…');
  try {
    await bridge.ready();
    if(!configSnapshot)renderConfig(await bridge.apiGet('config'));
    // Read overview first: the persisted Debug setting gates all later presentation.
    overview(await bridge.apiGet('overview'));
    if(requestedTab==='lyrics')lyrics(await bridge.apiGet('lyrics',{offset,limit:PAGE_SIZE}));
    if(requestedTab==='contexts')contexts(await bridge.apiGet('contexts'));
    if(requestedTab==='history')history(await bridge.apiGet('history'));
    if(requestedTab==='configuration'){
      if(!configDirty&&!configBusy)renderConfig(await bridge.apiGet('config'));
      if(debugMode)processorDebug(await bridge.apiGet('processor/debug'));
    }
    $('updated-at').textContent=`更新于 ${new Date().toLocaleTimeString('zh-CN',{hour12:false})}`;
    status(configDirty?'运行数据已更新；有未保存的配置修改。':'运行数据已同步');
  } catch {status('数据读取失败，请重试；已有内容保留。',true);}
  finally {
    loading=false;$('refresh').disabled=false;$('refresh').replaceChildren(icon('refresh'),node('span','刷新数据'));$('refresh').removeAttribute('aria-busy');$(requestedTab).removeAttribute('aria-busy');
    syncConfigAvailability();
    if(requestedTab==='lyrics')renderLyrics();
    if(requestedTab!==activeTab)refresh();
  }
}
function navigate(tab) {
  activeTab=tab;detailEpoch++;
  document.querySelectorAll('[data-tab]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.tab===tab)));
  document.querySelectorAll('section.panel').forEach(panel=>panel.hidden=panel.id!==tab);
  refresh();
}
document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>navigate(button.dataset.tab)));
document.querySelectorAll('[data-go]').forEach(button=>button.addEventListener('click',()=>navigate(button.dataset.go)));
function userRefresh(event) {
  const now=performance.now();
  if(event.detail>1||now-lastRefreshClick<600)return;
  lastRefreshClick=now;refresh();
}
$('refresh').addEventListener('click',userRefresh);$('retry').addEventListener('click',userRefresh);
$('previous').addEventListener('click',()=>{if(!loading){offset=Math.max(0,offset-PAGE_SIZE);refresh();}});
$('next').addEventListener('click',()=>{if(!loading){offset+=PAGE_SIZE;refresh();}});
$('lyrics-search').addEventListener('input',renderLyrics);
$('contexts-search').addEventListener('input',()=>{pages.contexts=0;renderContexts();});
$('history-search').addEventListener('input',()=>{pages.history=0;renderHistory();});
$('close-detail').addEventListener('click',()=>$('detail').close());
$('detail').addEventListener('close',()=>{detailEpoch++;$('detail-content').replaceChildren();});
for(const [id,endpoint,title] of [['clear-cache','cache/clear','确认清空所有音乐缓存？'],['clear-history','history/clear','确认清空近期回复？']]) $(id).addEventListener('click',()=>{
  pendingClear=endpoint;$('confirmation-title').textContent=title;$('confirmation').showModal();
});
$('cancel-clear').addEventListener('click',()=>{pendingClear=null;$('confirmation').close();});
$('confirm-clear').addEventListener('click',async()=>{
  if(!bridge||!pendingClear)return;
  const endpoint=pendingClear;pendingClear=null;$('confirmation').close();
  for(const id of ['clear-cache','clear-history'])$(id).disabled=true;
  try {await bridge.apiPost(endpoint,{confirm:true});offset=0;await refresh();}
  catch {status('清理未完成，请检查存储权限或稍后重试。',true);}
  finally {for(const id of ['clear-cache','clear-history'])$(id).disabled=false;}
});
// The native schema supplies every editable field; no second defaults/schema.
function renderConfig(snapshot) {
  configSnapshot=snapshot;
  applyDebug(snapshot.values.debug_mode);
  $('debug-toggle').disabled=!snapshot.writable||configBusy;
  $('probe-result').textContent='';
  configDirty=false;
  const fields=$('config-fields');
  fields.replaceChildren();
  function group(schema,values,parent,path=[]) {
    for(const [key,definition] of Object.entries(schema)) {
      const parts=[...path,key];
      if(definition.type==='object') {
        const fieldset=node('div');
        fieldset.append(node('h3',definition.description||key));
        group(definition.items,values[key],fieldset,parts);
        parent.append(fieldset);
        continue;
      }
      const row=node('div',undefined,'config-field');
      const label=node('label',definition.description||key);
      const modelSelect=definition._special==='select_provider';
      const input=node(modelSelect||definition.options?'select':'input');
      input.id='config-'+parts.join('-'); label.htmlFor=input.id;
      input.dataset.path=JSON.stringify(parts); input.dataset.kind=definition.type;
      if(modelSelect) {
        input.append(new Option('请选择 AstrBot 已配置模型',''));
        for(const model of snapshot.models||[]) input.append(new Option(model.label,model.id));
        if(values[key] && !(snapshot.models||[]).some(model=>model.id===values[key])) {
          const missing=new Option('已不可用 · '+values[key],values[key]);
          missing.disabled=true; input.append(missing);
        }
      }
      else if(definition.options) for(const option of definition.options) input.append(new Option(option,option));
      else input.type=definition.type==='bool'?'checkbox':['int','float'].includes(definition.type)?'number':'text';
      if(definition.type==='bool') input.checked=values[key]; else input.value=values[key];
      for(const [source,target] of [['minimum','min'],['maximum','max'],['max_length','maxLength']]) if(definition[source]!==undefined) input[target]=definition[source];
      if(definition.type==='float') input.step='any';
      input.disabled=!snapshot.writable||configBusy;
      input.addEventListener('input',()=>{configDirty=true;$('config-status').textContent='有未保存的修改。';});
      row.append(label,input);
      if(definition.hint){const hint=node('small',definition.hint,'muted config-hint');hint.id=input.id+'-hint';input.setAttribute('aria-describedby',hint.id);row.append(hint);}
      parent.append(row);
    }
  }
  const groups = [
    ['基础配置', ['enable_card_parser','history_enable','max_history_items','store_llm_history','llm_history_limit']],
    ['缓存设置', ['enable_cache','cache_ttl','max_cache_items','show_full_lyrics_preview']],
    ['Context 设置', ['enable_context_injection','max_context_length']],
    ['Processor 设置', ['enable_context_processor','processor_model','context_processor']],
    ['Debug 设置', ['debug_mode']],
  ];
  const rendered=new Set();
  for(const [title,keys] of groups) {
    const fieldset=node('fieldset');fieldset.append(node('legend',title));
    const schema={};for(const key of keys)if(key in snapshot.schema){schema[key]=snapshot.schema[key];rendered.add(key);}
    group(schema,snapshot.values,fieldset);fields.append(fieldset);
  }
  const remaining=Object.fromEntries(Object.entries(snapshot.schema).filter(([key])=>!rendered.has(key)));
  if(Object.keys(remaining).length){const fieldset=node('fieldset');fieldset.append(node('legend','其他配置'));group(remaining,snapshot.values,fieldset);fields.append(fieldset);}
  $('config-access').textContent=snapshot.writable?'可编辑 · 原生配置':'只读';
  const toggle=$('config-enable_context_processor');
  const model=$('config-processor_model');
  const syncProcessor=()=>{
    model.disabled=!snapshot.writable||!toggle.checked||configBusy;
    $('test-processor').disabled=!toggle.checked||!model.value||configBusy;
  };
  toggle.addEventListener('change',syncProcessor);
  model.addEventListener('change',syncProcessor);
  syncProcessor();
  $('save-config').disabled=!snapshot.writable;
  $('reset-config').disabled=!snapshot.writable;
  syncConfigAvailability();
  $('config-status').textContent=snapshot.writable?'配置已读取。保存使用 AstrBot 原生配置文件。':'当前配置只读；请检查 AstrBot 配置持久化支持。';
}
function formValues() {
  const values={};
  for(const input of $('config-fields').querySelectorAll('[data-path]')) {
    const path=JSON.parse(input.dataset.path);
    let target=values;
    for(const part of path.slice(0,-1)) target=target[part]??=( {} );
    target[path.at(-1)]=input.dataset.kind==='bool'?input.checked:['int','float'].includes(input.dataset.kind)?Number(input.value):input.value;
  }
  return values;
}
function processorDebug(data) {
  const names={status:'状态',model:'模型',version:'Prompt 版本',processed_at:'处理时间',kind:'请求类型',input_length:'输入字符数',output_fields:'输出字段',token_estimate:'token 估计',latency_ms:'耗时（毫秒）',attempts:'尝试次数',input_truncated:'输入已缩减',error:'错误代码',retry_reason:'重试原因'};
  $('processor-debug').replaceChildren();
  if(!debugMode)return;
  const rows=Object.entries(data).filter(([key])=>!['processor_input','processor_output'].includes(key)).map(([key,value])=>[names[key]||key,value]);
  $('processor-debug').append(fields('处理状态',rows),debugSections(data,['processor_input','processor_output']));
}
function syncConfigAvailability() {
  const busy=configBusy||loading, readonly=!configSnapshot?.writable;
  $('debug-toggle').disabled=busy||readonly;
  for(const input of $('config-fields').querySelectorAll('input,select'))input.disabled=busy||readonly;
  if($('config-processor_model'))$('config-processor_model').disabled=busy||readonly||!$('config-enable_context_processor').checked;
  for(const id of ['save-config','reset-config'])$(id).disabled=busy||readonly;
  $('reload-config').disabled=busy;
  $('test-processor').disabled=busy||!$('config-enable_context_processor')?.checked||!$('config-processor_model')?.value;
}
async function configOperation(operation) {
  if(configBusy||!bridge)return;
  if(loading){status('数据正在刷新，请稍后重试配置操作。');return;}
  configBusy=true;
  $('debug-toggle').disabled=true;
  $('config-form').setAttribute('aria-busy','true');
  for(const input of $('config-fields').querySelectorAll('input,select'))input.disabled=true;
  $('config-status').textContent='正在处理…';
  for(const id of ['save-config','reload-config','reset-config','test-processor']) {$(id).disabled=true;$(id).setAttribute('aria-busy','true');}
  try {await operation();}
  catch {$('config-status').textContent='操作失败。请检查参数；若配置已被其他页面修改，请重新读取后保存。';status('配置操作失败，请重新读取后重试。',true);}
  finally {
    configBusy=false;
    for(const id of ['save-config','reload-config','reset-config','test-processor'])$(id).removeAttribute('aria-busy');
    $('config-form').removeAttribute('aria-busy');
    for(const input of $('config-fields').querySelectorAll('input,select'))input.disabled=!configSnapshot?.writable;
    if($('config-processor_model'))$('config-processor_model').disabled=!configSnapshot?.writable||!$('config-enable_context_processor')?.checked;
    $('debug-toggle').checked=debugMode;
    $('debug-toggle').disabled=!configSnapshot?.writable;
    $('reload-config').disabled=false;
    $('test-processor').disabled=!$('config-enable_context_processor')?.checked||!$('config-processor_model')?.value;
    syncConfigAvailability();
  }
}
$('config-form').addEventListener('submit',event=>{
  event.preventDefault();
  configOperation(async()=>{
    renderConfig(await bridge.apiPost('config/save',{values:formValues(),revision:configSnapshot.revision}));
    $('config-status').textContent='配置已保存，新请求使用新配置。';
    await refresh();
  });
});
$('debug-toggle').addEventListener('change',()=>configOperation(async()=>{
  $('debug-toggle').checked=debugMode;
  if(configDirty){$('config-status').textContent='请先保存或重新读取配置，再切换 Debug。';status('有未保存的配置，请先保存或重新读取后再切换 Debug。');return;}
  const values={...configSnapshot.values,debug_mode:!debugMode};
  renderConfig(await bridge.apiPost('config/save',{values,revision:configSnapshot.revision}));
  await refresh();
}));
$('reload-config').addEventListener('click',()=>configOperation(async()=>renderConfig(await bridge.apiGet('config'))));
$('reset-config').addEventListener('click',()=>$('reset-confirmation').showModal());
$('cancel-reset').addEventListener('click',()=>$('reset-confirmation').close());
$('confirm-reset').addEventListener('click',()=>{
  $('reset-confirmation').close();
  configOperation(async()=>{renderConfig(await bridge.apiPost('config/reset',{confirm:true,revision:configSnapshot.revision}));await refresh();});
});
$('test-processor').addEventListener('click',()=>configOperation(async()=>{
  if(configDirty){$('probe-result').textContent='请先保存或重新读取配置，再测试连接。';return;}
  $('probe-result').textContent='正在发送固定测试请求…';
  const result=await bridge.apiPost('processor/test',{confirm:true});
  $('probe-result').textContent=`${result.success?'连接成功':'连接失败'} · ${result.model||'未设置模型'} · ${result.latency_ms} ms${result.error?' · '+result.error:''}`;
  processorDebug(await bridge.apiGet('processor/debug'));
}));
if(bridge) await refresh();
else {status('请从 AstrBot WebUI 的插件详情打开此管理页。',true);$('refresh').disabled=true;}
