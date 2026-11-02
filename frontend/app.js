let project = null;
let selectedSchemeId = null;
let availableFonts = [];
let subtitleStyleTimer = null;
let selectedCueIndex = 0;
let audioPreviewUrl = null;
let verifiedPlan = null;
let acceptedRotationPlanId = null;
let configurationRevision = 0;
let preflightRequestSequence = 0;
let renderingBatch = false;
const $ = id => document.getElementById(id);
function invalidatePlan(reason = '配置已改变，请重新预检') {
  configurationRevision += 1;
  verifiedPlan = null;
  acceptedRotationPlanId = null;
  $('acceptRotation').classList.add('hidden');
  $('renderSelected').disabled = true;
  $('planStatus').textContent = reason;
  $('batchOverlapSummary').textContent = '近期最高 Body 复用率：待重新计算';
  $('searchSummary').textContent = '';
}
const overlapText = report => report?.max_pair_overlap == null ? '仅1条，无两两比较对象' : `${(report.max_pair_overlap * 100).toFixed(1)}%`;
const api = async (url, options = {}) => {
  if (options.method === 'POST' && !/\/(settings|preflight|render|audio-preview|pre-render)$/.test(url)) invalidatePlan();
  const res = await fetch(url, options); const data = await res.json().catch(() => ({}));
  if (!res.ok) { const detail = data.detail; throw Error(typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `请求失败 ${res.status}`); }
  return data;
};
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function log(message, kind = '') { const el = document.createElement('div'); el.className = `log-line ${kind}`; el.textContent = message; $('statusLog').prepend(el); $('logCount').textContent = document.querySelectorAll('.log-line').length; }
function setStatusPanel(panel) { document.querySelectorAll('[data-panel-content]').forEach(item => item.classList.toggle('hidden', item.dataset.panelContent !== panel)); document.querySelectorAll('.status-tabs [data-panel]').forEach(button => { const active = button.dataset.panel === panel; button.classList.toggle('active', active); button.setAttribute('aria-selected', active ? 'true' : 'false'); }); }
function renderTaskStatus(status, detail = '') { $('taskList').innerHTML = `<div class="task-status"><strong>${escapeHtml(status)}</strong><div class="task-progress"><span style="width:${status.includes('完成') ? 100 : status.includes('渲染中') ? 12 : 0}%"></span></div><div class="muted">${escapeHtml(detail)}</div></div>`; }
function expectedOutputDirectory() { if (project?.output_dir) return project.output_dir; const excel = project?.excel_path || ''; const parent = excel.replace(/[\\/][^\\/]*$/, ''); return `${parent}\\VideoMagicMix-输出`; }
function renderDedupe(report, planned, completed = 0) {
  if (!report) { $('dedupeSummary').innerHTML = '<div class="muted">尚无预检数据</div>'; return; }
  const seconds = n => Number(n || 0).toFixed(2);
  const filename = path => String(path).split(/[\\/]/).pop();
  const percent = n => `${(Number(n) * 100).toFixed(2)}%`;
  const pairRows = (report.pair_details || []).map(p => `<details class="rotation-detail"><summary class="${p.exceeds ? 'bad' : ''}">第${p.left}条 ↔ 第${p.right}条：${percent(p.ratio)}${p.exceeds ? ' · 超目标' : ''}</summary><div>共用 ${seconds(p.shared_seconds)} 秒 ÷ 较短 Body ${seconds(p.denominator_seconds)} 秒</div>${p.contributions.map(g => `<div>${escapeHtml(g.row_key)}：共用 ${seconds(g.shared_seconds)} 秒</div>`).join('')}</details>`).join('');
  const preview = (report.preview_jobs || []).map(job => `<details class="rotation-detail"><summary>第${job.number}条 · ${escapeHtml(job.scheme_id)}</summary><div>Hook：${escapeHtml(filename(job.hook))}</div>${job.bodies.map(b => `<div>${escapeHtml(b.row_key)}：${b.files.map(f => escapeHtml(filename(f))).join(' + ')} · ${Number(b.speed).toFixed(3)}×</div>`).join('')}</details>`).join('');
  const groups = (report.groups || []).map(g => `<div class="rotation-detail">${escapeHtml(g.scheme_id)} / ${escapeHtml(g.row_key)}：${g.candidate_count}个候选，实际涉及${g.eligible_source_count}个素材；每个候选都包含的画面至少 ${seconds(g.unavoidable_seconds)} 秒 / ${seconds(g.target_duration)} 秒</div>`).join('');
  const usage = (report.source_usage || []).map(u => `<div class="rotation-detail" title="${escapeHtml(u.path)}">${escapeHtml(u.scheme_id)} / ${escapeHtml(u.row_key)} / ${escapeHtml(filename(u.path))}：${u.count}次 · ${seconds(u.seconds)}秒${u.count === 0 ? '（本批未使用，可能不在合法候选中）' : ''}</div>`).join('');
  $('dedupeSummary').innerHTML = `<div class="dedupe-value">近期最高 Body 复用率 ${overlapText(report)}</div><div>预检计划 ${planned} 条 · 已生成 ${completed || 0} 条</div><div>与前${report.recent_window || 3}条逐对比较：${report.pair_count || 0}对；超标${report.violating_pairs || 0}对</div><div>目标 ${report.upper_limit == null ? '未设（只报告）' : percent(report.upper_limit)} · 平均 ${report.average_pair_overlap == null ? '无比较' : percent(report.average_pair_overlap)}</div><div>完整选材重复 ${report.duplicate_count || 0}条</div><div class="muted">仅报告近期 Body 素材复用；不含 Hook，不是视觉查重或全批次任意两条保证。明细按复用率从高到低排列${report.details_truncated ? '，仅展示最高100对，统计包含全部受检配对' : ''}。</div>${(report.duplicate_groups || []).map(g => `<div class="bad">完整组合重复：第 ${g.join('、')} 条</div>`).join('')}<details open><summary>复用明细</summary>${pairRows || '暂无比较对象'}</details><details><summary>前10条选材安排</summary>${preview}</details><details><summary>分组变化能力</summary>${groups}</details><details><summary>素材曝光次数与秒数</summary>${usage}</details><details><summary>Hook轮换次数</summary>${Object.entries(report.hook_usage || {}).map(([path, count]) => `<div>${escapeHtml(filename(path))}：${count}次</div>`).join('')}</details>`;
}
function renderMessages() { $('errors').textContent = (project?.errors || []).join('\n'); $('warnings').textContent = (project?.warnings || []).join('\n'); $('errors').style.display = project?.errors?.length ? 'block' : 'none'; $('warnings').style.display = project?.warnings?.length ? 'block' : 'none'; }
function renderBgm() {
  $('bgmPath').value = project?.bgm_path || '';
  $('bgmStatus').textContent = project?.bgm_path ? '仅 Body，按配音时长裁剪/循环' : '可选（仅 Body）';
  for (const [id, field, fallback] of [['bgmVolume','bgm_volume',0.3],['voiceVolume','voice_volume',1],['hookVolume','hook_volume',1]]) {
    $(id).value = Math.round((project?.[field] ?? fallback) * 100);
    $(`${id}Value`).value = `${$(id).value}%`;
  }
  $('maxOverlap').value = project?.max_overlap == null ? '' : Number((project.max_overlap * 100).toFixed(3));
  $('recentWindow').value = project?.recent_window ?? 3;
}
function renderOutputSettings() { if (!project) return; for (const [id, key] of [['outputFormat','output_format'],['outputVideoCodec','output_video_codec'],['outputResolution','output_resolution'],['outputFps','output_fps'],['outputVideoBitrate','output_video_bitrate'],['outputAudioBitrate','output_audio_bitrate'],['outputAudioSampleRate','output_audio_sample_rate']]) if (project[key] != null) $(id).value = project[key]; }
function renderAudioSegments() {
  const previous = $('audioPreviewSegment').value;
  const segments = currentScheme()?.segments || [];
  $('audioPreviewSegment').innerHTML = '<option value="">配音前12秒</option><option value="Hook">第一个 Hook 原声（不加 BGM）</option>' + segments.filter(s => s.enabled && s.row_key.toLowerCase() !== 'hook').map(s => `<option value="${escapeHtml(s.row_key)}" ${s.start == null ? 'disabled' : ''}>${escapeHtml(s.row_key)}：${escapeHtml(s.text)}${s.start == null ? '（等待识别）' : ''}</option>`).join('');
  if ([...$('audioPreviewSegment').options].some(o => o.value === previous && !o.disabled)) $('audioPreviewSegment').value = previous;
}
function bodyRowsForSelectedScheme() { const scheme = project?.schemes?.find(item => item.scheme_id === ($('schemeSelect').value || selectedSchemeId)); return scheme ? scheme.preview.filter(item => item.row_key.toLowerCase() !== 'hook') : []; }
function bodyConfig(schemeId, rowKey) { return project?.body_configs?.[schemeId]?.[rowKey] || {}; }
function renderBodyPreview() {
  const scheme = project?.schemes?.find(item => item.scheme_id === ($('schemeSelect').value || selectedSchemeId));
  const rescanButton = $('rescanScheme');
  if (!scheme) { $('bodyPreview').innerHTML = '<span class="muted">导入 Excel 后显示当前字幕方案的 Body 文案</span>'; if (rescanButton) { rescanButton.disabled = true; rescanButton.onclick = null; } return; }
  selectedSchemeId = scheme.scheme_id;
  renderAudioSegments();
  if ($('bodyRootDir')) $('bodyRootDir').value = project?.body_root_dir || '';
  if (rescanButton) { rescanButton.disabled = !scheme.voice_path; rescanButton.textContent = scheme.voice_path ? '重新识别并扫描' : '请先上传配音'; rescanButton.onclick = () => retryTranscription(scheme.scheme_id, rescanButton); }
  $('bodyPreview').innerHTML = bodyRowsForSelectedScheme().map(item => {
    if (!item.enabled) return `<div class="body-preview-row skipped"><div class="body-row-title"><b>${escapeHtml(item.row_key)}</b><span>${escapeHtml(item.group_name || '未命名分组')}</span></div><code>文案为空，已跳过</code></div>`;
    const config = bodyConfig(scheme.scheme_id, item.row_key);
    const hasScan = config.target_duration != null;
    const metrics = hasScan ? `素材 ${config.scanned_video_count ?? 0} 个 / 可用组合 ${config.actual_candidate_count ?? 0} 个` : '等待配音识别和素材扫描';
    const noVideoHint = hasScan && Number(config.scanned_video_count || 0) === 0 ? ' / 目录内没有视频文件' : '';
    const mappedPath = project.body_mapping?.[item.row_key] || '';
    return `<div class="body-preview-entry"><div class="body-preview-row"><div class="body-row-title"><b>${escapeHtml(item.row_key)}</b><span>${escapeHtml(item.group_name || '未命名分组')}</span></div><code>${escapeHtml(item.preview || '')}</code><input class="body-folder-path" title="${escapeHtml(mappedPath)}" value="${escapeHtml(mappedPath)}" placeholder="请选择该 Body 文件夹" readonly><button class="outline body-folder-button" data-body-folder="${escapeHtml(item.row_key)}">添加</button><button class="outline body-clear-button" data-body-clear="${escapeHtml(item.row_key)}">清除</button>
      <label class="body-inline-field">候选<input type="number" min="1" step="1" value="${escapeHtml(config.candidate_count ?? 0)}" data-body-candidate="${escapeHtml(item.row_key)}"></label>
      <label class="body-inline-field">最低倍速<input type="number" min="0.001" step="0.001" value="${escapeHtml(config.min_speed ?? 0.9)}" data-body-min-speed="${escapeHtml(item.row_key)}"></label>
      <label class="body-inline-field">最高倍速<input type="number" min="0.001" step="0.001" value="${escapeHtml(config.threshold ?? 1.4)}" data-body-threshold="${escapeHtml(item.row_key)}"></label>
      <span class="body-recommended">${config.user_configured ? '当前' : '默认'}范围 ${Number(config.min_speed ?? 0.9).toFixed(3)}–${Number(config.threshold ?? 1.4).toFixed(3)}×</span>
      <span class="body-metrics ${noVideoHint ? 'body-no-video' : ''}" data-body-metrics="${escapeHtml(item.row_key)}">${escapeHtml(metrics)}${config.target_duration ? ` / 目标 ${Number(config.target_duration).toFixed(2)}秒` : ''}${escapeHtml(noVideoHint)}</span>
      <div class="body-combination-actions"><button class="outline body-combinations-button" data-body-combinations="${escapeHtml(item.row_key)}" data-scope="current">现有候选数量的预览</button><button class="outline body-combinations-button" data-body-combinations="${escapeHtml(item.row_key)}" data-scope="all">所有组合预览</button><button class="outline" data-body-prerender="${escapeHtml(item.row_key)}">预渲染片段</button></div>
      </div><div class="body-combination-preview hidden" data-combination-list="${escapeHtml(item.row_key)}"></div></div>`;
  }).join('') || '<span class="muted">没有 Body 行</span>';
  document.querySelectorAll('[data-body-folder]').forEach(button => button.onclick = () => chooseBodyFolder(button.dataset.bodyFolder));
  document.querySelectorAll('[data-body-clear]').forEach(button => button.onclick = () => clearBodyFolder(button.dataset.bodyClear));
  document.querySelectorAll('[data-body-combinations]').forEach(button => button.onclick = () => previewBodyCombinations(scheme.scheme_id, button.dataset.bodyCombinations, button.dataset.scope, button));
  document.querySelectorAll('[data-body-candidate]').forEach(input => input.onchange = () => updateBodyConfig(scheme.scheme_id, input.dataset.bodyCandidate, 'candidate_count', input.value));
  document.querySelectorAll('[data-body-threshold]').forEach(input => input.onchange = () => updateBodyConfig(scheme.scheme_id, input.dataset.bodyThreshold, 'threshold', input.value));
  document.querySelectorAll('[data-body-min-speed]').forEach(input => input.onchange = () => updateBodyConfig(scheme.scheme_id, input.dataset.bodyMinSpeed, 'threshold', document.querySelector(`[data-body-threshold="${input.dataset.bodyMinSpeed}"]`).value));
  document.querySelectorAll('[data-body-prerender]').forEach(button => button.onclick = () => prerenderBody(scheme.scheme_id, button.dataset.bodyPrerender, button));
}
function schemeStatus(scheme) { if (scheme.match_status === 'matched') return '匹配成功'; if (scheme.match_status === 'failed') return `匹配失败：${scheme.error || '请检查配音'}`; if (scheme.asr_status === 'uploaded') return '已上传，等待识别'; return '未上传配音'; }
function renderSchemes() { $('schemePanel').classList.remove('hidden'); $('schemeSelect').disabled = !project?.schemes?.length; $('schemeSelect').innerHTML = (project?.schemes || []).map(s => `<option value="${escapeHtml(s.scheme_id)}">${escapeHtml(s.display_name)} / ${escapeHtml(s.source_column)}</option>`).join(''); if (!selectedSchemeId && project?.schemes?.length) selectedSchemeId = project.schemes[0].scheme_id; $('schemeSelect').value = selectedSchemeId || ''; renderBodyPreview(); renderSubtitleStyle(); $('schemeList').innerHTML = (project?.schemes || []).map(scheme => `<article class="scheme-row"><div><strong>${escapeHtml(scheme.display_name)} / ${escapeHtml(scheme.source_column)}</strong><span class="status ${scheme.match_status === 'failed' ? 'bad' : ''}" data-status="${escapeHtml(scheme.scheme_id)}">${escapeHtml(schemeStatus(scheme))}</span><div class="scheme-preview">${scheme.preview.map(item => `<span>${escapeHtml(item.row_key)}：${escapeHtml(item.enabled ? item.preview : item.skip_reason || '跳过')}</span>`).join('')}</div><div class="scheme-error ${scheme.error ? 'bad' : ''}" data-error="${escapeHtml(scheme.scheme_id)}">${escapeHtml(scheme.error || '')}</div></div><input type="file" accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,.opus" data-voice="${escapeHtml(scheme.scheme_id)}"><button type="button" class="outline" data-upload="${escapeHtml(scheme.scheme_id)}">上传并识别</button><button type="button" class="outline" data-retry="${escapeHtml(scheme.scheme_id)}" ${scheme.voice_path ? '' : 'disabled'}>重新识别并扫描</button></article>`).join(''); document.querySelectorAll('[data-upload]').forEach(button => button.onclick = () => uploadVoice(button.dataset.upload)); document.querySelectorAll('[data-retry]').forEach(button => button.onclick = () => retryTranscription(button.dataset.retry, button)); }
function currentScheme() { return project?.schemes?.find(item => item.scheme_id === ($('schemeSelect').value || selectedSchemeId)); }
function renderSubtitleStyle() {
  const style = project?.subtitle_style || {font_name: availableFonts[0]?.name || 'Microsoft YaHei', font_size: 52, bold: true, primary_color: '#FFFFFF', outline_color: '#000000', outline_width: 3, shadow: 1, alignment: 2, margin_vertical: 120, max_width: 90, max_chars_per_cue: 10};
  if ($('subtitleFont')) $('subtitleFont').value = style.font_name || availableFonts[0]?.name || '';
  if ($('subtitleFontSize')) $('subtitleFontSize').value = style.font_size ?? 52;
  if ($('subtitleBold')) $('subtitleBold').checked = style.bold !== false;
  if ($('subtitlePrimaryColor')) $('subtitlePrimaryColor').value = style.primary_color || '#FFFFFF';
  if ($('subtitleOutlineColor')) $('subtitleOutlineColor').value = style.outline_color || '#000000';
  if ($('subtitleOutlineWidth')) $('subtitleOutlineWidth').value = style.outline_width ?? 3;
  if ($('subtitleShadow')) $('subtitleShadow').value = style.shadow ?? 1;
  if ($('subtitleAlignment')) $('subtitleAlignment').value = style.alignment ?? 2;
  if ($('subtitleMargin')) $('subtitleMargin').value = style.margin_vertical ?? 120;
  if ($('subtitleMaxWidth')) $('subtitleMaxWidth').value = style.max_width ?? 90;
  if ($('subtitleMaxChars')) $('subtitleMaxChars').value = style.max_chars_per_cue ?? 10;
  const scheme = currentScheme();
  const previous = $('subtitlePreviewSegment')?.value;
  if ($('subtitlePreviewSegment')) {
    $('subtitlePreviewSegment').innerHTML = (scheme?.preview || []).filter(item => item.enabled && item.row_key.toLowerCase() !== 'hook').map(item => `<option value="${escapeHtml(item.row_key)}">${escapeHtml(item.row_key)} / ${escapeHtml(item.preview || '')}</option>`).join('');
    if (previous && [...$('subtitlePreviewSegment').options].some(option => option.value === previous)) $('subtitlePreviewSegment').value = previous;
  }
  renderSubtitleCues();
}

function splitPreviewText(text, limit) { const phrases = String(text || '').split(/[\r\n，,、；;。.!！?？]+/).map(value => value.replace(/\s+/g, '')).filter(Boolean); return phrases.flatMap(value => { const out = []; for (let i = 0; i < value.length; i += limit) out.push(value.slice(i, i + limit)); return out; }); }
function currentPreviewCues() { const scheme = currentScheme(); const rowKey = $('subtitlePreviewSegment')?.value; const segment = scheme?.segments?.find(item => item.row_key === rowKey); if (!segment) return []; if (segment.subtitle_cues?.length) return segment.subtitle_cues; return splitPreviewText(segment.text, Number(project?.subtitle_style?.max_chars_per_cue || 10)).map(text => ({text, matched:false})); }
function renderSubtitleCues() { const cues = currentPreviewCues(); if (!cues.length) { $('subtitleCueList').innerHTML = '<span class="muted">等待选择 Body 文案</span>'; return; } selectedCueIndex = Math.min(selectedCueIndex, cues.length - 1); $('subtitleCueList').innerHTML = cues.map((cue, index) => `<button type="button" class="subtitle-cue ${index === selectedCueIndex ? 'active' : ''}" data-cue-index="${index}"><span>${index + 1}. ${escapeHtml(cue.text)}</span><small>${cue.matched ? `${Number(cue.start_seconds).toFixed(2)}–${Number(cue.end_seconds).toFixed(2)} 秒` : '等待识别'}</small></button>`).join(''); document.querySelectorAll('[data-cue-index]').forEach(button => button.onclick = () => { selectedCueIndex = Number(button.dataset.cueIndex); renderSubtitleCues(); refreshSubtitlePreview(); }); }
async function loadFonts() {
  try {
    const result = await api('/api/fonts');
    availableFonts = result.fonts || [];
    $('subtitleFont').innerHTML = availableFonts.map(font => `<option value="${escapeHtml(font.name)}">${escapeHtml(font.name)}${font.source === 'bundled' ? ' / 内置' : ''}${font.supports_chinese ? '' : ' / 可能缺少中文'}</option>`).join('');
    $('fontStats').textContent = `可用字体 ${result.count}，中文标记 ${result.chinese_count}`;
    renderSubtitleStyle();
  } catch (error) { $('fontStats').textContent = '字体列表读取失败'; log(`字体列表读取失败：${error.message}`, 'error'); }
}
function subtitleStyleForm() { const fd = new FormData(); fd.append('font_name', $('subtitleFont').value); fd.append('font_size', $('subtitleFontSize').value); fd.append('bold', $('subtitleBold').checked ? 'true' : 'false'); fd.append('primary_color', $('subtitlePrimaryColor').value); fd.append('outline_color', $('subtitleOutlineColor').value); fd.append('outline_width', $('subtitleOutlineWidth').value); fd.append('shadow', $('subtitleShadow').value); fd.append('alignment', $('subtitleAlignment').value); fd.append('margin_vertical', $('subtitleMargin').value); fd.append('max_width', $('subtitleMaxWidth').value); fd.append('max_chars_per_cue', $('subtitleMaxChars').value); return fd; }
function scheduleSubtitleStyleSave() { clearTimeout(subtitleStyleTimer); subtitleStyleTimer = setTimeout(saveSubtitleStyle, 250); }
async function saveSubtitleStyle() { if (!project) return; try { project = await api(`/api/projects/${project.project_id}/subtitle-style`, { method: 'POST', body: subtitleStyleForm() }); renderSubtitleStyle(); await refreshSubtitlePreview(); } catch (error) { log(`字幕样式保存失败：${error.message}`, 'error'); } }
async function refreshSubtitlePreview() { const image = $('subtitlePreview'); const message = $('subtitlePreviewMessage'); renderSubtitleCues(); if (!project || !currentScheme() || !$('subtitlePreviewSegment').value) { image.classList.add('hidden'); message.textContent = '导入 Excel 并选择 Body 素材后显示真实字幕预览'; return; } const rowKey = $('subtitlePreviewSegment').value; image.classList.add('hidden'); message.textContent = '正在生成真实字幕预览...'; image.onload = () => { image.classList.remove('hidden'); message.textContent = ''; }; image.onerror = () => { image.classList.add('hidden'); message.textContent = '暂无可预览素材，请先选择对应 Body 文件夹'; }; image.src = `/api/projects/${encodeURIComponent(project.project_id)}/subtitle-preview?scheme_id=${encodeURIComponent(currentScheme().scheme_id)}&row_key=${encodeURIComponent(rowKey)}&cue_index=${selectedCueIndex}&t=${Date.now()}`; }
async function waitForTranscription(taskId, id, button) { const clientStarted = Date.now(); let requestFailures = 0; for (;;) { let task; try { task = await api(`/api/tasks/${taskId}`); requestFailures = 0; } catch (error) { requestFailures += 1; if (requestFailures >= 5) throw Error(`无法读取识别进度：${error.message}`); await new Promise(resolve => setTimeout(resolve, 1500)); continue; } const elapsed = task.elapsed_seconds ?? Math.floor((Date.now() - clientStarted) / 1000); button.textContent = task.status === 'running' ? `识别中 ${elapsed}秒` : '等待识别...'; const status = document.querySelector(`[data-status="${id}"]`); if (status) status.textContent = task.message || button.textContent; if (task.status === 'completed' || task.status === 'failed') return task; if (Date.now() - clientStarted > 31 * 60 * 1000) throw Error('识别任务超过 31 分钟且没有结束，请重启软件后重新识别'); await new Promise(resolve => setTimeout(resolve, 1000)); } }
async function uploadVoice(id) { const input = document.querySelector(`[data-voice="${id}"]`); const button = document.querySelector(`[data-upload="${id}"]`); if (!input?.files?.[0]) return alert('请选择配音文件'); const fd = new FormData(); fd.append('file', input.files[0]); button.disabled = true; button.textContent = '上传中...'; try { log(`上传 ${id} 配音并开始 ASR`); await api(`/api/projects/${project.project_id}/schemes/${id}/voice`, { method: 'POST', body: fd }); const task = await api(`/api/projects/${project.project_id}/schemes/${id}/transcribe`, { method: 'POST' }); const result = await waitForTranscription(task.task_id, id, button); const refreshed = await api(`/api/projects/${project.project_id}`); project = refreshed; renderSchemes(); if (result.status === 'completed') log(`${id} 字幕匹配完成，Body 素材已重新扫描`, 'success'); else log(`${id} 字幕匹配失败：${result.error || result.message}`, 'error'); } catch (error) { log(error.message, 'error'); alert(error.message); } finally { const current = document.querySelector(`[data-upload="${id}"]`); if (current) { current.disabled = false; current.textContent = '上传并识别'; } } }
async function retryTranscription(id, button) { if (!project) return; const scheme = project.schemes?.find(item => item.scheme_id === id); if (!scheme?.voice_path) return alert('请先上传该字幕方案的配音'); button = button || document.querySelector(`[data-retry="${id}"]`); if (button) { button.disabled = true; button.textContent = '重新识别中...'; } try { log(`${id} 手动重新开始配音识别和素材扫描`); const task = await api(`/api/projects/${project.project_id}/schemes/${id}/transcribe/retry`, { method: 'POST' }); const result = await waitForTranscription(task.task_id, id, button || document.querySelector(`[data-upload="${id}"]`)); const refreshed = await api(`/api/projects/${project.project_id}`); project = refreshed; renderMessages(); renderSchemes(); if (result.status === 'completed') log(`${id} 重新识别完成，Body 素材扫描已刷新`, 'success'); else log(`${id} 重新识别失败：${result.error || result.message}`, 'error'); } catch (error) { log(`重新识别失败：${error.message}`, 'error'); alert(error.message); } finally { const current = document.querySelector(`[data-retry="${id}"]`); if (current) { current.disabled = false; current.textContent = '重新识别并扫描'; } } }
async function renderSchemesBatch() {
  if (!project) return alert('请先导入 Excel');
  if (!verifiedPlan) return alert('请先预检并确认本批次数量和重复率');
  if (verifiedPlan.requires_acceptance && acceptedRotationPlanId !== verifiedPlan.plan_id) return alert('请先查看选材明细，再明确接受当前未达标安排');
  const button = $('renderSelected');
  renderingBatch = true;
  button.disabled = true;
  window.videoMagicMix?.setRendering(true);
  try {
    await saveSettings(false);
    if (!verifiedPlan) throw Error('配置已改变，请重新预检');
    const confirmed = verifiedPlan;
    const form = new FormData(); form.append('plan_id', confirmed.plan_id);
    form.append('accept_warnings', acceptedRotationPlanId === confirmed.plan_id ? 'true' : 'false');
    const renderTaskId = crypto.randomUUID(); form.append('task_id', renderTaskId);
    log(`开始渲染已锁定的 ${confirmed.planned_outputs} 条；近期最高 Body 复用率：${overlapText(confirmed.overlap)}`);
    $('taskCount').textContent = `0 / ${confirmed.planned_outputs}`;
    renderTaskStatus(`渲染中 0 / ${confirmed.planned_outputs}`, '正在生成并检查视频，请稍候…');
    $('planStatus').textContent = `正在渲染已确认的 ${confirmed.planned_outputs} 条组合`;
    let renderDone = false;
    let result;
    const renderRequest = api(`/api/projects/${project.project_id}/render`, { method: 'POST', body: form }).then(value => { result = value; renderDone = true; return value; }).catch(error => { renderDone = true; throw error; });
    while (!renderDone) {
      await new Promise(resolve => setTimeout(resolve, 700));
      try {
        const task = await api(`/api/render-tasks/${renderTaskId}`);
        $('taskCount').textContent = `${task.completed || 0} / ${task.total || confirmed.planned_outputs}`;
        $('outputCount').textContent = task.healthy_count || 0;
        $('outputHealth').textContent = `健康视频 ${task.healthy_count || 0} 条`;
        $('openOutputDir').disabled = false;
        $('openOutputDir').dataset.path = expectedOutputDirectory();
        $('outputList').innerHTML = (task.outputs || []).map(output => `<div class="output-item">✓ ${escapeHtml(output)}</div>`).join('');
        if (task.overlap) renderDedupe(task.overlap, confirmed.planned_outputs, task.healthy_count || 0);
        renderTaskStatus(`渲染中 ${task.completed || 0} / ${task.total || confirmed.planned_outputs}`, task.current_file ? `当前文件：${task.current_file}` : '正在生成并检查视频…');
      } catch (_) { /* The render request may still be acquiring its task slot. */ }
    }
    await renderRequest;
    const healthyCount = result.healthy_count ?? result.count;
    $('outputCount').textContent = healthyCount;
    $('outputHealth').textContent = `健康视频 ${$('outputCount').textContent} 条`;
    $('openOutputDir').disabled = false;
    $('openOutputDir').dataset.path = expectedOutputDirectory();
    $('outputList').innerHTML = result.outputs.map(output => `<div class="output-item">✓ ${escapeHtml(output)}</div>`).join('');
    if (result.overlap) {
      const report = result.overlap;
      log(`本批次使用预检锁定的组合；实际生成 ${result.count} 条，近期最高 Body 复用率：${overlapText(report)}`);
    }
    $('capacitySummary').textContent = `理论产能 ${result.estimated_outputs} 条，本次已生成 ${result.count} 条`;
    $('capacitySummary').dataset.planned = result.count;
    renderTaskStatus(`渲染完成 ${result.count} / ${confirmed.planned_outputs}`, `健康视频 ${healthyCount} 条`);
    $('taskCount').textContent = `${result.count} / ${confirmed.planned_outputs}`;
    renderDedupe(result.overlap, confirmed.planned_outputs, healthyCount);
    log(`批量渲染完成，共生成 ${healthyCount} 条健康视频`, 'success');
  } catch (error) {
    renderTaskStatus('渲染失败', error.message);
    log(`渲染失败：${error.message}`, 'error');
    alert(error.message);
  } finally {
    renderingBatch = false;
    invalidatePlan('本批次已结束；再次生成前请重新预检');
    window.videoMagicMix?.setRendering(false);
  }
}
async function importXlsx() { const file = $('xlsx').files[0]; if (!file) return alert('请选择 Excel 文件'); const fd = new FormData(); fd.append('file', file); try { project = await api('/api/projects/import-xlsx', { method: 'POST', body: fd }); selectedSchemeId = project.schemes[0]?.scheme_id || null; $('outputLimit').value = project.output_limit || 0; $('capacitySummary').textContent = '预计产能：未预检'; $('capacitySummary').dataset.planned = ''; $('projectState').textContent = `已导入：${project.project_name} / ${project.sheet_name}`; renderMessages(); renderBgm(); renderOutputSettings(); renderSchemes(); log(`已导入 ${project.project_name}`, project.errors?.length ? 'error' : 'success'); } catch (error) { $('errors').textContent = error.message; $('errors').style.display = 'block'; log(error.message, 'error'); } }
async function saveSettings(showLog = true, refreshBody = true) {
  if (!project) return project;
  const rawLimit = $('outputLimit').value.trim();
  if (!/^\d*$/.test(rawLimit)) throw Error('生成数量必须是大于或等于 0 的整数');
  const fd = new FormData();
  fd.append('hook_dir', $('hookDir').value);
  fd.append('body_root_dir', $('bodyRootDir')?.value || '');
  fd.append('output_dir', $('outputDir').value);
  fd.append('output_limit', rawLimit || '0');
  fd.append('max_overlap', $('maxOverlap').value.trim());
  fd.append('recent_window', $('recentWindow').value);
  for (const [id, field] of [['bgmVolume','bgm_volume'],['voiceVolume','voice_volume'],['hookVolume','hook_volume']]) fd.append(field, (Number($(id).value) / 100).toFixed(2));
  const outputKeys = [['outputFormat','output_format'],['outputVideoCodec','output_video_codec'],['outputResolution','output_resolution'],['outputFps','output_fps'],['outputVideoBitrate','output_video_bitrate'],['outputAudioBitrate','output_audio_bitrate'],['outputAudioSampleRate','output_audio_sample_rate']];
  for (const [id, key] of outputKeys) fd.append(key, $(id).value);
  const settingsKeys = ['hook_dir','body_root_dir','output_dir','output_limit','max_overlap','recent_window','bgm_volume','voice_volume','hook_volume', ...outputKeys.map(([, key]) => key)];
  const before = JSON.stringify(settingsKeys.map(key => project[key] ?? null));
  const revision = configurationRevision;
  project = await api(`/api/projects/${project.project_id}/settings`, { method: 'POST', body: fd });
  if (revision !== configurationRevision) throw Error('保存期间配置已改变，请重新预检');
  if (before !== JSON.stringify(settingsKeys.map(key => project[key] ?? null))) invalidatePlan();
  renderMessages(); if (refreshBody) renderBodyPreview(); renderBgm(); renderOutputSettings();
  $('outputLimit').value = project.output_limit || 0;
  if (showLog) log('素材目录、三路音量、重复率和生成数量已保存', 'success');
  return project;
}
async function updateBodyConfig(schemeId, rowKey, field, value) {
  const fd = new FormData(); fd.append('scheme_id', schemeId); fd.append('row_key', rowKey);
  fd.append(field, value); fd.append('changed_by', field);
  if (field === 'threshold') fd.append('min_speed', document.querySelector(`[data-body-min-speed="${rowKey}"]`).value);
  try {
    const result = await api(`/api/projects/${project.project_id}/body-config`, { method: 'POST', body: fd });
    project = result.project; renderMessages(); renderBodyPreview();
    log(`${schemeId} / ${rowKey}：倍速 ${result.config.min_speed}–${result.config.threshold}×，已勾选 ${result.config.candidate_count} 个组合`, 'success');
    await preflight();
  } catch (error) { log(`Body 配置更新失败：${error.message}`, 'error'); alert(error.message); renderBodyPreview(); }
}
async function previewBodyCombinations(schemeId, rowKey, scope, button, offset = 0, force = false) {
  const panel = document.querySelector(`[data-combination-list="${rowKey}"]`);
  if (!panel) return;
  if (!force && offset === 0 && !panel.classList.contains('hidden') && panel.dataset.scope === scope) { panel.classList.add('hidden'); return; }
  panel.dataset.scope = scope;
  panel.classList.remove('hidden'); panel.textContent = '正在读取组合...';
  if (button) button.disabled = true;
  try {
    const result = await api(`/api/projects/${project.project_id}/schemes/${encodeURIComponent(schemeId)}/bodies/${encodeURIComponent(rowKey)}/combinations?scope=${scope}&offset=${offset}`);
    const entries = result.combinations || [];
    panel.innerHTML = `<div class="body-combination-summary">${escapeHtml(rowKey)}：${scope === 'all' ? '全部枚举' : '当前范围'} ${result.candidate_count} 个，已勾选 <b data-selected-count>${result.selected_count}</b> 个；范围 ${result.min_speed}–${result.threshold}×，目标 ${Number(result.target_duration).toFixed(2)} 秒</div>
      <div class="muted">${escapeHtml(result.enumeration_note)}<br>点击“预渲染片段”才生成已勾选组合的视频。预览静音，仅展示变速后的画面。</div>
      <ol start="${offset + 1}">${entries.map((item, index) => `<li class="${item.within_current_threshold ? 'combination-accepted' : ''}">
        <label class="combo-check"><input type="checkbox" data-combo-check="${index}" ${item.selected ? 'checked' : ''} ${item.within_current_threshold ? '' : 'disabled'}>参与混剪</label>
        <button class="combo-watch" data-combo-watch="${index}">${item.files.map(escapeHtml).join(' + ')}</button>
        <small>原始 ${Number(item.source_duration).toFixed(2)} 秒 → ${Number(item.target_duration).toFixed(2)} 秒（实际 ${Number(item.playback_speed).toFixed(3)}×）${item.within_current_threshold ? '' : ' / 范围外，请先调整倍速范围'} / ${item.preview_url ? '可播放预览' : '尚未预渲染'}</small></li>`).join('')}</ol>
      <div class="combo-pagination"><button class="outline" data-prev ${offset === 0 ? 'disabled' : ''}>上一页</button><span>${entries.length ? offset + 1 : 0}–${offset + entries.length} / ${result.candidate_count}</span><button class="outline" data-next ${result.next_offset == null ? 'disabled' : ''}>下一页</button></div>
      <video class="body-combo-video hidden" controls preload="none"></video><div class="combo-message muted"></div>`;
    panel.querySelector('[data-prev]').onclick = () => previewBodyCombinations(schemeId, rowKey, scope, button, Math.max(0, offset - 100), true);
    panel.querySelector('[data-next]').onclick = () => previewBodyCombinations(schemeId, rowKey, scope, button, result.next_offset, true);
    panel.querySelectorAll('[data-combo-watch]').forEach(watch => watch.onclick = () => {
      const item = entries[Number(watch.dataset.comboWatch)];
      const message = panel.querySelector('.combo-message');
      if (!item.selected) { message.textContent = '此组合未勾选，不参与当前混剪。请先勾选，再点击预渲染片段。'; return; }
      if (!item.preview_url) { message.textContent = '尚未生成该组合的视频，请先点击此 Body 的“预渲染片段”。'; return; }
      const player = panel.querySelector('video'); player.src = item.preview_url; player.classList.remove('hidden');
      message.textContent = `${item.files.join(' + ')} / 实际 ${Number(item.playback_speed).toFixed(3)}× / 静音预览`;
      player.play().catch(error => { message.textContent = `播放失败：${error.message}`; });
    });
    panel.querySelectorAll('[data-combo-check]').forEach(checkbox => checkbox.onchange = async () => {
      const item = entries[Number(checkbox.dataset.comboCheck)];
      panel.querySelectorAll('[data-combo-check]').forEach(control => control.disabled = true);
      try {
        const fd = new FormData(); fd.append('identity', item.id); fd.append('selected', checkbox.checked ? 'true' : 'false');
        const response = await api(`/api/projects/${project.project_id}/schemes/${schemeId}/bodies/${rowKey}/selection`, {method:'POST', body:fd});
        item.selected = checkbox.checked;
        project.body_configs[schemeId][rowKey] = response.config;
        document.querySelector(`[data-body-candidate="${rowKey}"]`).value = response.config.actual_candidate_count;
        document.querySelector(`[data-body-metrics="${rowKey}"]`).textContent = `素材 ${response.config.scanned_video_count} 个 / 可用组合 ${response.config.actual_candidate_count} 个 / 目标 ${Number(response.config.target_duration).toFixed(2)}秒`;
        panel.querySelector('[data-selected-count]').textContent = response.config.actual_candidate_count;
        if (!item.selected) panel.querySelector('video').pause();
        await preflight({keepBodyPreview:true});
      } catch (error) { checkbox.checked = item.selected; log(`组合勾选失败：${error.message}`, 'error'); }
      finally { panel.querySelectorAll('[data-combo-check]').forEach(control => control.disabled = !entries[Number(control.dataset.comboCheck)].within_current_threshold); }
    });
  } catch (error) { panel.textContent = error.message; log(`${schemeId} / ${rowKey} 组合预览失败：${error.message}`, 'error'); }
  finally { if (button) button.disabled = false; }
}
async function prerenderBody(schemeId, rowKey, button) {
  button.disabled = true;
  const projectId = project.project_id;
  try {
    let task = await api(`/api/projects/${projectId}/schemes/${schemeId}/bodies/${rowKey}/pre-render`, {method:'POST'});
    log(`${rowKey} 开始预渲染 ${task.total} 个已勾选组合（静音画面）`);
    while (task.status === 'queued' || task.status === 'running') {
      button.textContent = `预渲染 ${task.completed + task.skipped}/${task.total}`;
      await new Promise(resolve => setTimeout(resolve, 1000));
      task = await api(`/api/preview-tasks/${task.task_id}`);
    }
    for (const error of task.errors || []) log(`${rowKey} 预渲染错误：${error}`, 'error');
    log(`${rowKey} 预渲染完成 ${task.completed} 个，跳过已取消 ${task.skipped} 个`, task.status === 'completed' ? 'success' : 'error');
    if (project?.project_id === projectId && currentScheme()?.scheme_id === schemeId) {
      const panel = document.querySelector(`[data-combination-list="${rowKey}"]`);
      await previewBodyCombinations(schemeId, rowKey, panel?.dataset.scope || 'current', null, 0, true);
    }
  } catch (error) { log(`${rowKey} 预渲染失败：${error.message}`, 'error'); alert(error.message); }
  finally { button.disabled = false; button.textContent = '预渲染片段'; }
}
async function mapBodyFolder(rowKey, directory) { const fd = new FormData(); fd.append('group_key', rowKey); fd.append('directory', directory); project = await api(`/api/projects/${project.project_id}/mapping/body`, { method: 'POST', body: fd }); renderBodyPreview(); log(directory ? `${rowKey} 目录已设置` : `${rowKey} 目录已清除`, 'success'); await refreshSubtitlePreview(); }
async function chooseFolder(kind) { if (!window.videoMagicMix?.selectDirectory) { alert('当前是浏览器备用模式，请在 Electron 桌面版中使用文件夹选择器。'); return; } const value = await window.videoMagicMix.selectDirectory(kind); if (!value) return; const id = kind === 'hook' ? 'hookDir' : kind === 'body-root' ? 'bodyRootDir' : 'outputDir'; $(id).value = value; await saveSettings(); if (kind === 'body-root') { log('已按 Excel 分组名称扫描并匹配 Body 子目录', 'success'); await preflight(); } }
async function chooseBodyFolder(rowKey) { if (!window.videoMagicMix?.selectDirectory) { alert('当前是浏览器备用模式，请在 Electron 桌面版中使用文件夹选择器。'); return; } const value = await window.videoMagicMix.selectDirectory('body', rowKey); if (!value) return; try { await mapBodyFolder(rowKey, value); } catch (error) { log(error.message, 'error'); alert(error.message); } }
function clearFolder(kind) { const id = kind === 'hook' ? 'hookDir' : 'outputDir'; $(id).value = ''; saveSettings(); }
async function uploadBgm() {
  const file = $('bgmFile').files[0];
  if (!file || !project) return;
  const button = $('selectBgm');
  const form = new FormData();
  form.append('file', file);
  button.disabled = true;
  button.textContent = '正在添加...';
  try {
    project = await api(`/api/projects/${project.project_id}/bgm`, { method: 'POST', body: form });
    renderBgm();
    log(`已添加背景音乐：${file.name}`, 'success');
  } catch (error) {
    log(`添加背景音乐失败：${error.message}`, 'error');
    alert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = '添加音频';
    $('bgmFile').value = '';
  }
}
async function clearBgm() {
  if (!project) return;
  try {
    project = await api(`/api/projects/${project.project_id}/bgm/clear`, { method: 'POST' });
    renderBgm();
    $('audioMixPreview').pause();
    $('audioMixPreview').classList.add('hidden');
    $('audioMixPreview').removeAttribute('src');
    log('背景音乐已清除', 'success');
  } catch (error) { log(`清除背景音乐失败：${error.message}`, 'error'); alert(error.message); }
}
async function previewAudioMix() {
  if (!project || !currentScheme()) return alert('请先导入 Excel 并选择字幕方案');
  const button = $('previewAudioMix');
  button.disabled = true;
  button.textContent = '正在生成试听...';
  try {
    await saveSettings(false, false);
    const form = new FormData();
    form.append('scheme_id', currentScheme().scheme_id);
    form.append('row_key', $('audioPreviewSegment').value);
    const response = await fetch(`/api/projects/${project.project_id}/audio-preview`, { method: 'POST', body: form });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw Error(error.detail || `试听生成失败（${response.status}）`);
    }
    const blob = await response.blob();
    if (audioPreviewUrl) URL.revokeObjectURL(audioPreviewUrl);
    audioPreviewUrl = URL.createObjectURL(blob);
    const player = $('audioMixPreview');
    player.src = audioPreviewUrl;
    player.classList.remove('hidden');
    await player.play();
    log(`正在试听：${$('audioPreviewSegment').selectedOptions[0]?.textContent || '配音前12秒'}。${$('audioPreviewSegment').value === 'Hook' ? 'Hook 仅播放原声，不加 BGM。' : 'BGM 从 Body 配音开始播放，与所选段落对齐。'}`, 'success');
  } catch (error) {
    log(`音频试听失败：${error.message}`, 'error');
    alert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = '试听所选片段';
  }
}
async function clearBodyFolder(rowKey) { const button = document.querySelector(`[data-body-clear="${rowKey}"]`); const row = button?.closest('.body-preview-row'); const pathInput = row?.querySelector('.body-folder-path'); if (pathInput) pathInput.value = ''; try { await mapBodyFolder(rowKey, ''); } catch (error) { log(error.message, 'error'); alert(error.message); } }
$('import').onclick = importXlsx; $('preflight').onclick = preflight; $('renderSelected').onclick = renderSchemesBatch; $('schemeSelect').onchange = () => { selectedCueIndex = 0; renderBodyPreview(); renderSubtitleStyle(); refreshSubtitlePreview(); }; $('subtitlePreviewSegment').onchange = () => { selectedCueIndex = 0; renderSubtitleCues(); refreshSubtitlePreview(); }; $('refreshSubtitlePreview').onclick = refreshSubtitlePreview; ['subtitleFont','subtitleFontSize','subtitleBold','subtitlePrimaryColor','subtitleOutlineColor','subtitleOutlineWidth','subtitleShadow','subtitleAlignment','subtitleMargin','subtitleMaxWidth','subtitleMaxChars'].forEach(id => { $(id).addEventListener('change', scheduleSubtitleStyleSave); $(id).addEventListener('input', scheduleSubtitleStyleSave); }); $('clearLog').onclick = () => { $('statusLog').innerHTML = ''; $('logCount').textContent = '0'; }; document.querySelectorAll('[data-folder]').forEach(button => button.onclick = () => chooseFolder(button.dataset.folder)); document.querySelectorAll('[data-clear]').forEach(button => button.onclick = () => clearFolder(button.dataset.clear));
document.querySelectorAll('.output-panel .output-grid select').forEach(input => input.addEventListener('change', () => saveSettings(false, false).catch(error => { log(`输出参数保存失败：${error.message}`, 'error'); alert(error.message); })));
$('renderSelected').addEventListener('click', () => setStatusPanel('tasks'));
document.querySelectorAll('.status-tabs [data-panel]').forEach(button => button.onclick = () => setStatusPanel(button.dataset.panel));
$('openOutputDir').onclick = async () => { try { await window.videoMagicMix?.openDirectory($('openOutputDir').dataset.path); } catch (error) { log(`打开产出目录失败：${error.message}`, 'error'); } };
$('selectBodyRoot').onclick = () => chooseFolder('body-root'); $('clearBodyRoot').onclick = async () => { $('bodyRootDir').value = ''; await saveSettings(); log('Body 素材根目录已清除；已有的手动目录映射保留', 'success'); };
$('selectBgm').onclick = () => $('bgmFile').click(); $('bgmFile').onchange = uploadBgm; $('clearBgm').onclick = clearBgm; $('previewAudioMix').onclick = previewAudioMix;
for (const id of ['bgmVolume','voiceVolume','hookVolume']) {
  $(id).oninput = () => { $(`${id}Value`).value = `${$(id).value}%`; };
  $(id).onchange = () => saveSettings(false, false).catch(error => { log(`保存音量失败：${error.message}`, 'error'); alert(error.message); });
}
for (const id of ['maxOverlap', 'outputLimit', 'recentWindow']) $(id).onchange = () => preflight({keepBodyPreview:true});
$('viewRotationDetails').onclick = () => setStatusPanel('dedupe');
$('acceptRotation').onclick = () => {
  if (!verifiedPlan || renderingBatch) return;
  acceptedRotationPlanId = verifiedPlan.plan_id;
  $('renderSelected').disabled = false;
  $('acceptRotation').classList.add('hidden');
  $('planStatus').textContent = '已明确接受当前安排；目标未达标的明细保留，可开始渲染';
  log('用户已接受当前安排（包括已报告的超标、重复组合或数量不足），不代表全部达标');
};
document.addEventListener('input', event => {
  if (event.target.matches('#outputLimit,#maxOverlap,#recentWindow,#voiceVolume,#bgmVolume,#hookVolume,.subtitle-style-controls input,.subtitle-style-controls select,[data-body-candidate],[data-body-threshold],[data-body-min-speed]')) invalidatePlan();
});
$('checkUpdates').onclick = async () => { const button = $('checkUpdates'); button.disabled = true; try { if (window.videoMagicMix?.installDownloadedUpdate && button.dataset.install === 'true') await window.videoMagicMix.installDownloadedUpdate(); else await window.videoMagicMix?.checkForUpdates(); } catch (error) { $('updateStatus').textContent = ''; alert(`更新操作失败：${error.message}`); } finally { if (button.dataset.busy !== 'true') button.disabled = false; } };
window.videoMagicMix?.onUpdateStatus(status => { const button = $('checkUpdates'); const label = $('updateStatus'); label.textContent = status.text || ''; button.dataset.busy = status.busy ? 'true' : 'false'; button.dataset.install = status.canInstall ? 'true' : 'false'; button.textContent = status.canInstall ? '安装已下载更新' : status.busy && status.percent != null ? `下载中 ${status.percent}%` : status.busy ? '正在检查...' : '检查更新'; button.disabled = status.busy; });
api('/api/health').then(data => { $('health').textContent = `${data.name} 已连接`; }).catch(() => { $('health').textContent = '后端未连接'; $('health').className = 'bad'; }); loadFonts();

// Keep the detailed preflight report in the right-hand log as well as the diagnostic panel.
async function preflight({keepBodyPreview = false} = {}) {
  if (!project) return alert('请先导入 Excel');
  if (renderingBatch) return log('正在渲染已确认的组合，本批次结束后可重新预检');
  const sequence = ++preflightRequestSequence;
  invalidatePlan('正在预检并计算本批次重复率…');
  $('preflight').textContent = '计算中…';
  try {
    await saveSettings(false, !keepBodyPreview);
    if (sequence !== preflightRequestSequence) return;
    const revision = configurationRevision;
    const projectId = project.project_id;
    const result = await api(`/api/projects/${project.project_id}/preflight`, { method: 'POST', body: new URLSearchParams() });
    if (sequence !== preflightRequestSequence || revision !== configurationRevision || project?.project_id !== projectId) return;
    if (result.body_configs) { project.body_configs = result.body_configs; if (!keepBodyPreview) renderBodyPreview(); }
    $('preflightResult').classList.remove('hidden');
    $('preflightResult').textContent = JSON.stringify(result, null, 2);
    const planned = result.planned_outputs ?? Math.min(result.estimated_outputs || 0, result.output_limit || result.estimated_outputs || 0);
    $('taskCount').textContent = result.ok ? `0 / ${planned}` : `${result.failures.length} 个问题`;
    $('capacitySummary').textContent = `预计产能：${result.estimated_outputs || 0} 条，本次计划：${planned} 条`;
    if (result.overlap) {
      const report = result.overlap;
      $('capacitySummary').textContent = `理论组合数：${result.estimated_outputs}；本次安排：${planned} 条`;
      $('batchOverlapSummary').textContent = `近期最高 Body 复用率：${overlapText(report)}`;
      $('searchSummary').textContent = `均衡轮换；与前${report.recent_window || 3}条比较 ${report.pair_count} 对，超目标 ${report.violating_pairs || 0} 对。完整组合重复 ${report.duplicate_count || 0} 条。${report.upper_limit == null ? '未设目标，只报告。' : `目标 ${(report.upper_limit * 100).toFixed(1)}%。`}${report.batch_limited ? '本批达到5000条上限，请分批生成。' : ''}${report.shortage ? '安排数量少于请求数量（理论组合数或批次上限），并非因复用目标缩减。' : ''}不是全批次任意两条或视觉相似度保证。`;
      log(`选材预检：${$('capacitySummary').textContent}；${$('batchOverlapSummary').textContent}。${$('searchSummary').textContent}`);
      renderDedupe(report, planned);
    }
    $('hookStatus').textContent = `已扫描 ${result.hook_count || 0} 个 Hook，全部作为完整候选`;
    $('capacitySummary').dataset.planned = planned;
    if (result.bgm) log(`BGM 时长 ${Number(result.bgm.source_duration).toFixed(2)} 秒，音量 ${Math.round(result.bgm.volume * 100)}%；${result.bgm.alignment}`, 'success');
    const estimates = result.scheme_estimates || [];
    for (const item of (result.plan || []).filter(item => item.row_key?.toLowerCase() !== 'hook').reverse()) {
      const emptyDirectory = Number(item.scanned_video_count ?? 0) === 0 && item.folder;
      log(`${item.display_name || item.scheme_id} / ${item.row_key}：${emptyDirectory ? `目录已匹配但没有视频：${item.folder}；请放入素材或修正 Excel 分组名。` : ''}倍速 ${item.min_speed ?? 0.9}–${item.threshold ?? '--'}×，已勾选候选 ${item.actual_candidate_count ?? item.candidate_count ?? 0} 个，素材 ${item.scanned_video_count ?? 0} 个，目标 ${Number(item.target_duration ?? item.target ?? 0).toFixed(2)} 秒`, item.actual_candidate_count ? 'success' : 'error');
    }
    for (const estimate of [...estimates].reverse()) {
      log(`${estimate.display_name}：预计 ${estimate.estimated_outputs} 条，本次计划 ${estimate.planned_outputs ?? estimate.estimated_outputs} 条，候选数量 ${estimate.candidate_counts.join(' × ')}`, estimate.estimated_outputs ? 'success' : 'error');
    }
    if (result.ok) {
      if (result.plan_id && planned > 0) {
        verifiedPlan = result;
        $('renderSelected').disabled = Boolean(result.requires_acceptance);
        $('acceptRotation').classList.toggle('hidden', !result.requires_acceptance);
        $('planStatus').textContent = result.requires_acceptance ? `已安排 ${planned} 条，但存在超标、重复或数量不足；请查看明细并接受当前安排` : `已锁定 ${planned} 条，近期复用检查达标，可开始渲染`;
        renderTaskStatus(`等待渲染 0 / ${planned}`, '预检已锁定组合，点击“开始渲染”后执行');
      }
      log(`${result.requires_acceptance ? '素材检查通过；选材安排需确认' : '素材与近期复用检查通过'}，理论组合 ${result.estimated_outputs}，本次安排 ${planned} 条`, result.requires_acceptance ? '' : 'success');
      $('errors').textContent = '';
      $('errors').style.display = 'none';
    } else {
      $('planStatus').textContent = '预检未通过，请按日志修正后重新预检';
      log(`预检发现 ${result.failures.length} 个问题，当前预计 ${result.estimated_outputs} 条`, 'error');
      for (const failure of [...result.failures].reverse()) log(`预检失败：${failure}`, 'error');
      $('errors').textContent = result.failures.join('\\n');
      $('errors').style.display = 'block';
    }
  } catch (error) {
    log(`预检请求失败：${error.message}`, 'error');
    if (sequence === preflightRequestSequence) $('planStatus').textContent = '预检未完成，请重新预检';
  } finally {
    if (sequence === preflightRequestSequence) $('preflight').textContent = '预检并计算';
  }
}
