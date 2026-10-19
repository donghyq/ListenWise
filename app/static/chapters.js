/* 桌面端按章查看正文、音频和修改稿；草稿在切换章节时保留。 */
function chapterDraftKey(contentId, sectionId) { return contentId + ':' + sectionId; }
function chapterDraft(content, chapter) {
  appState.chapterDrafts ||= {};
  const key = chapterDraftKey(content.id, chapter.id);
  const draft = appState.chapterDrafts[key] ||= { mode: '', instructions: '', body: chapter.body, base_version: chapter.version, candidate: null, error: '', busy: false };
  if (!draft.mode && !draft.candidate && !draft.busy && draft.base_version !== chapter.version) {
    draft.body = chapter.body; draft.base_version = chapter.version;
  }
  return draft;
}
function chapterAudio(content, chapter) {
  const titles = [content.title + ' · ' + chapter.heading];
  if ((content.chapters || []).length === 1 || chapter.heading === content.title) titles.push(content.title);
  return (appState.selectedProject.audio_outputs || [])
    .filter(output => output.narrative_content_id === content.id && titles.includes(output.title))
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.updated_at.localeCompare(a.updated_at))[0];
}
function chapterAudioLabel(audio) {
  if (!audio) return '未生成音频';
  if (audio.status === 'source_changed') return '音频待更新';
  if (audio.audio_available) return '音频已就绪';
  return '脚本已就绪';
}
function renderChapterWorkbench(content, options = {}) {
  const showSidebar = options.sidebar !== false;
  const chapters = content.chapters || [];
  if (!chapters.length) return '<p>请刷新项目以读取章节。</p>';
  appState.chapterFocus ||= {};
  const progress = content.reading_progress || {};
  const focused = chapters.find(c => c.id === appState.chapterFocus[content.id])
    || chapters.find(c => c.reading_id === progress.last_section_id) || chapters[0];
  appState.chapterFocus[content.id] = focused.id;
  const completed = new Set(progress.completed_section_ids || []);
  const draft = chapterDraft(content, focused);
  const audio = chapterAudio(content, focused);
  const attr = 'data-content-id="' + content.id + '" data-section-id="' + escapeHtml(focused.id) + '"';
  const button = (action, text, cls = 'button-outline', disabled = false) => '<button class="button button-small ' + cls + '" data-chapter-action="' + action + '" ' + attr + (disabled ? ' disabled' : '') + '>' + text + '</button>';
  const audioEnabled = appState.selectedProject.project.audio_enabled;
  const nav = showSidebar ? '<aside class="chapter-sidebar" aria-label="章节目录"><div class="chapter-sidebar-inner"><div class="chapter-sidebar-head"><b>章节目录</b><small>' + chapters.length + ' 章 · 已学 ' + chapters.filter(c => completed.has(c.reading_id)).length + ' 章 · ' + chapters.filter(c => chapterAudio(content, c)?.audio_available).length + ' 章音频就绪</small></div><div class="chapter-nav">' + chapters.map((chapter, index) => {
    const track = chapterAudio(content, chapter);
    const pending = appState.chapterDrafts?.[chapterDraftKey(content.id, chapter.id)];
    return '<button type="button" class="chapter-nav-item' + (chapter.id === focused.id ? ' active' : '') + '" data-chapter-select="' + content.id + '" data-section-id="' + escapeHtml(chapter.id) + '" aria-pressed="' + (chapter.id === focused.id) + '"><span class="chapter-number">' + String(index + 1).padStart(2, '0') + '</span><span><b>' + escapeHtml(chapter.heading) + '</b><small>' + chapter.body.length + ' 字' + (audioEnabled ? ' · ' + chapterAudioLabel(track) : '') + (pending?.candidate ? ' · 有候选稿' : pending?.mode === 'edit' ? ' · 编辑中' : '') + '</small></span>' + (completed.has(chapter.reading_id) ? '<i aria-label="已学">✓</i>' : '') + '</button>';
  }).join('') + '</div><button class="button button-quiet button-small" data-outline-feedback="' + content.outline_id + '">调整章节划分与顺序 →</button></div></aside>' : '';
  const player = audio?.audio_available
    ? '<div class="chapter-player"><audio controls preload="metadata" data-chapter-player="' + audio.id + '" src="/api/audio-outputs/' + audio.id + '/stream?v=' + encodeURIComponent(audio.updated_at) + '"></audio><label>倍速<select data-chapter-rate><option value="1">1×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label>' + button('export-audio', '保存 MP3') + '</div>'
    : '<p class="chapter-audio-hint">' + (audio?.status === 'source_changed' ? '本章正文已修改。确认讲稿后更新音频，手机端也会使用更新后的音频。' : '本章还没有可播放音频。' + (content.status !== 'confirmed' ? '先确认讲稿，再生成正式音频。' : '')) + '</p>';
  const audioBar = audioEnabled ? '<section class="chapter-audio-bar" aria-label="本章音频"><div class="chapter-audio-title"><b>本章音频</b><span class="tag">' + chapterAudioLabel(audio) + (audio?.audio_available ? ' · ' + formatListenTime(audio.duration_seconds || 0) : '') + '</span>' + button('audio', audio?.audio_available ? '重新生成' : audio?.status === 'source_changed' ? '更新本章音频' : '生成本章音频', 'button-outline', draft.busy) + '</div>' + player + '</section>' : '';
  let body = '<div class="chapter-body narrative-article">' + renderNarrativeHtml(focused.body, content.id) + '</div>';
  if (draft.mode === 'edit') body = '<label class="chapter-edit-label">修改本章正文<textarea class="chapter-editor" data-chapter-body ' + attr + '>' + escapeHtml(draft.body) + '</textarea></label><div class="chapter-action-row">' + button('save', '保存本章修改', 'button-primary', draft.busy) + button('cancel', '取消编辑', 'button-quiet', draft.busy) + '<small>保存后仅这一章和整篇音频需要更新。</small></div>';
  const feedback = draft.mode === 'feedback' || draft.candidate ? '<section class="chapter-feedback"><h4>这章希望怎样修改？</h4><p>可以引用具体段落，说明应保留、删减或重新解释的内容。只会生成本章候选稿。</p><div class="feedback-presets">' + ['减少重复、表达更简洁', '保留更多原文细节', '解释术语，降低理解难度', '调整论述顺序和衔接'].map(text => '<button class="button button-quiet button-small" data-feedback-preset="' + text + '" ' + attr + '>' + text + '</button>').join('') + '</div><label>修改意见<textarea data-chapter-feedback ' + attr + ' placeholder="例如：第二段重复了第一段的背景。请合并说明；保留原文的日期和限制条件，最后再解释它与本章主题的关系。">' + escapeHtml(draft.instructions) + '</textarea></label><div class="chapter-action-row">' + button('preview', draft.busy ? '正在生成候选稿…' : '按意见生成候选稿', 'button-primary', draft.busy) + button('cancel', '关闭修改意见', 'button-quiet', draft.busy) + '</div></section>' : '';
  const candidate = draft.candidate ? '<section class="chapter-comparison" aria-label="修改前后对照"><div class="chapter-comparison-head"><h4>检查候选稿</h4><p>尚未替换正文。你可以继续修改右侧文字，再决定是否采用。</p></div><div class="chapter-comparison-grid"><div><b>当前正文</b><div class="chapter-original narrative-article">' + renderNarrativeHtml(draft.candidate.original, content.id) + '</div></div><label>候选稿<textarea class="chapter-editor" data-chapter-candidate ' + attr + '>' + escapeHtml(draft.candidate.body) + '</textarea></label></div><div class="chapter-action-row">' + button('apply', '采用候选稿', 'button-primary', draft.busy) + button('discard', '保留原稿', 'button-outline', draft.busy) + '</div></section>' : '';
  const status = '<p class="chapter-status" role="status">' + (draft.busy ? '正在处理本章，请稍候…' : '') + '</p>';
  return '<article class="chapter-workbench" data-chapter-workbench="' + content.id + '"><div class="chapter-workbench-header"><div><h3>' + escapeHtml(content.title) + '</h3><p>' + chapters.length + ' 章 · <span class="status ' + content.status + '">' + statusText(content.status) + '</span></p></div><div class="chapter-action-row"><button class="button button-small button-primary" data-confirm-narrative="' + content.id + '">' + (content.status === 'confirmed' ? '讲稿已确认' : '确认讲稿') + '</button><button class="button button-small button-outline" data-toggle-narrative-view="' + content.id + '">编辑整篇</button><button class="button button-small button-outline" data-export-narrative="' + content.id + '">导出讲稿</button></div></div><div class="chapter-layout' + (showSidebar ? '' : ' no-sidebar') + '">' + nav + '<div class="chapter-main"><div class="chapter-heading"><span>第 ' + (chapters.indexOf(focused) + 1) + ' 章 / 共 ' + chapters.length + ' 章</span><h4>' + escapeHtml(focused.heading) + '</h4><small>' + focused.body.length + ' 字 · 原文依据 ' + focused.source_block_ids.length + ' 块</small></div>' + audioBar + '<div class="chapter-action-row chapter-tools">' + button('edit', '修改本章', 'button-outline', draft.busy) + button('feedback', '向 AI 提修改意见', 'button-outline', draft.busy) + button('sources', '查看原文依据', 'button-quiet', !focused.source_block_ids.length) + button('export-text', '导出本章', 'button-quiet') + button('learned', completed.has(focused.reading_id) ? '✓ 已学' : '标记已学', 'button-quiet') + '</div>' + (draft.error ? '<p class="message error" role="alert">' + escapeHtml(draft.error) + '</p>' : '') + status + (draft.candidate ? '' : body) + feedback + candidate + '</div></div></article>';
}
function hasUnsavedChapterEdits(contentId) {
  return Object.entries(appState.chapterDrafts || {}).some(([key, draft]) => key.startsWith(contentId + ':') && (draft.mode === 'edit' || draft.candidate));
}
function bindChapterWorkbench(root = $('#detail-panel')) {
  const panel = root;
  $$('[data-chapter-select]', panel).forEach(button => button.addEventListener('click', () => {
    stopOtherAudio();
    appState.chapterFocus[button.dataset.chapterSelect] = button.dataset.sectionId;
    renderActivePanel();
  }));
  $$('[data-chapter-action]', panel).forEach(button => button.addEventListener('click', () => runChapterAction(button)));
  $$('[data-chapter-body], [data-chapter-feedback], [data-chapter-candidate]', panel).forEach(input => input.addEventListener('input', () => {
    const draft = appState.chapterDrafts[chapterDraftKey(input.dataset.contentId, input.dataset.sectionId)];
    if (input.hasAttribute('data-chapter-feedback')) draft.instructions = input.value;
    else if (input.hasAttribute('data-chapter-candidate')) draft.candidate.body = input.value;
    else draft.body = input.value;
  }));
  $$('[data-feedback-preset]', panel).forEach(button => button.addEventListener('click', () => {
    const draft = appState.chapterDrafts[chapterDraftKey(button.dataset.contentId, button.dataset.sectionId)];
    draft.instructions = [draft.instructions, button.dataset.feedbackPreset].filter(Boolean).join('\n');
    $('[data-chapter-feedback]', button.closest('.chapter-feedback')).value = draft.instructions;
  }));
  $$('[data-outline-feedback]', panel).forEach(button => button.addEventListener('click', () => {
    appState.structureFeedbackOpen = true;
    appState.structureFeedbackOutline = button.dataset.outlineFeedback;
    appState.activeTab = 'structure'; renderProjectDetail();
    $('#outline-feedback')?.scrollIntoView({ block: 'start' });
  }));
  $$('audio[data-chapter-player]', panel).forEach(player => {
    const output = appState.selectedProject.audio_outputs.find(a => a.id === player.dataset.chapterPlayer);
    rememberAudioPosition(player, () => output);
    player.addEventListener('play', () => stopOtherAudio(player));
    $('[data-chapter-rate]', player.closest('.chapter-player')).addEventListener('change', event => { player.playbackRate = Number(event.target.value); });
  });
}
async function runChapterAction(button) {
  const content = appState.selectedProject.narrative_contents.find(c => c.id === button.dataset.contentId);
  const chapter = content.chapters.find(c => c.id === button.dataset.sectionId);
  const draft = chapterDraft(content, chapter);
  const action = button.dataset.chapterAction;
  if (draft.busy) return;
  draft.error = '';
  if (action === 'edit' || action === 'feedback') {
    if (draft.candidate && action === 'edit') { draft.error = '请先采用候选稿或保留原稿，再直接编辑正文。'; renderActivePanel(); return; }
    if (draft.mode === 'edit' && action !== 'edit' && draft.body !== chapter.body) { draft.error = '请先保存或取消本章编辑。'; renderActivePanel(); return; }
    draft.mode = action; renderActivePanel();
    $('[data-chapter-' + (action === 'edit' ? 'body' : 'feedback') + ']')?.focus(); return;
  }
  if (['cancel', 'discard'].includes(action)) {
    draft.mode = ''; draft.candidate = null; draft.body = chapter.body; draft.base_version = chapter.version;
    renderActivePanel(); return;
  }
  if (action === 'learned') { await saveReadingProgress(content.id, chapter.reading_id, !(content.reading_progress.completed_section_ids || []).includes(chapter.reading_id), false); return; }
  if (action === 'export-text') { await exportSection(content.id, chapter.id, button); return; }
  if (action === 'export-audio') { await exportAudioOutput(chapterAudio(content, chapter).id, button); return; }
  const projectId = appState.selectedProject.project.id;
  const endpoint = '/api/narrative-contents/' + content.id + '/sections/' + encodeURIComponent(chapter.id);
  try {
    draft.busy = true;
    if (action === 'sources') {
      const blocks = [];
      for (const doc of appState.selectedProject.documents) {
        const data = await request('/api/documents/' + doc.id);
        blocks.push(...data.blocks.filter(b => chapter.source_block_ids.includes(b.id)));
      }
      showSourceOverlay(chapter.heading + ' · 原文依据', blocks.map(b => '<h4>' + escapeHtml(b.source_locator) + '</h4><pre>' + escapeHtml(b.text) + '</pre>').join(''));
      return;
    }
    renderActivePanel();
    if (action === 'preview') {
      if (!draft.instructions.trim()) throw new Error('请先填写具体的修改意见。');
      draft.candidate = await request(endpoint + '/revision', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instructions: draft.instructions, base_version: chapter.version }) });
    } else if (action === 'save' || action === 'apply') {
      const candidate = action === 'apply' ? draft.candidate : null;
      await request(endpoint, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body: candidate?.body ?? draft.body, base_version: candidate?.base_version ?? draft.base_version, instructions: candidate?.instructions || '' }) });
      delete appState.chapterDrafts[chapterDraftKey(content.id, chapter.id)];
      appState.selectedProject = await request('/api/projects/' + projectId);
      showMessage('本章已保存。请确认讲稿后更新本章音频。');
    } else if (action === 'audio') {
      if (content.status !== 'confirmed') {
        // 不再禁用按钮——禁用时点击毫无反馈，用户以为坏了。明确告知原因并带到确认按钮。
        draft.error = '本章讲稿还未确认，所以不能生成正式 MP3。点上方「确认讲稿」后即可生成。';
        renderActivePanel();
        $('[data-confirm-narrative="' + content.id + '"]')?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        return;
      }
      if (hasUnsavedChapterEdits(content.id)) throw new Error('请先保存编辑或处理候选稿，再生成音频。');
      let audio = chapterAudio(content, chapter);
      if (!audio || audio.status === 'source_changed') audio = await request('/api/projects/' + projectId + '/audio-scripts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ narrative_content_id: content.id, section_heading: chapter.heading }) });
      await request('/api/audio-outputs/' + audio.id + '/synthesize', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      appState.selectedProject = await request('/api/projects/' + projectId);
      showMessage('本章音频已就绪。');
    }
  } catch (error) { draft.error = error.message; }
  finally { draft.busy = false; renderActivePanel(); if (action === 'preview' && draft.candidate) $('.chapter-comparison')?.scrollIntoView({ block: 'start', behavior: 'smooth' }); }
}

function renderOutlineFeedback(record) {
  if (!record) return '';
  return '<details class="panel-card outline-feedback" id="outline-feedback"' + (appState.structureFeedbackOpen ? ' open' : '') + '><summary>章节划分不合适？按意见重新规划</summary><p>说明哪些章节应合并、拆分或调整顺序。新大纲需要再次确认，已有讲稿和音频会保留。</p><label>结构调整意见<textarea id="outline-feedback-text" placeholder="例如：第一章和第二章背景重复，请合并；第三章内容过多，按原因与影响拆成两章。">' + escapeHtml(appState.outlineFeedbackText || '') + '</textarea></label><div class="chapter-action-row"><button class="button button-primary button-small" id="revise-outline-feedback">按意见生成新大纲</button><button class="button button-outline button-small" id="copy-outline-for-edit">复制大纲，手动调整</button></div><p id="outline-feedback-status" role="status"></p></details>';
}
function bindOutlineFeedback(record) {
  if (!record) return;
  const input = $('#outline-feedback-text');
  input?.addEventListener('input', () => { appState.outlineFeedbackText = input.value; });
  $('#copy-outline-for-edit')?.addEventListener('click', event => copyOutlineForEditing(record, event.currentTarget));
  $('#revise-outline-feedback')?.addEventListener('click', async event => {
    const button = event.currentTarget;
    const status = $('#outline-feedback-status');
    try {
      if (!input.value.trim()) throw new Error('请先说明章节应该怎样调整。');
      const outline = record.outline;
      const sourceIds = record.source_block_ids;
      const supplements = Object.keys(outline.supplemental_sources || {});
      setBusy(button, true, '正在重新规划…'); status.textContent = '正在按意见规划新的章节，原稿仍保留。';
      const result = await request('/api/projects/' + appState.selectedProject.project.id + '/narrative-outlines', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: record.document_id, title: record.title, source_block_ids: sourceIds, supplemental_document_ids: supplements, audience: record.audience, style_profile: record.style_profile, target_length: record.target_length === 'host' ? 'standard' : record.target_length, target_words: outline.target_total_words || 2400, transform_mode: outline.transform_mode || appState.selectedProject.project.transform_mode, web_research_mode: outline.web_research_mode || appState.selectedProject.project.web_research_mode, revision_of: record.id, revision_instructions: input.value.trim() }) });
      appState.selectedProject = await request('/api/projects/' + appState.selectedProject.project.id);
      appState.structureFeedbackOpen = false; appState.structureFeedbackOutline = result.id;
      renderProjectDetail(); $('#narrative-outline-area')?.scrollIntoView({ block: 'start' });
      showMessage('已生成待确认的新大纲。确认并生成新讲稿后，旧稿仍可查看。');
    } catch (error) { status.textContent = error.message; status.className = 'error'; }
    finally { if (button.isConnected) setBusy(button, false); }
  });
}
async function copyOutlineForEditing(record, button) {
  try {
    setBusy(button, true, '准备草稿…');
    const result = await request('/api/narrative-outlines/' + record.id + '/copy', { method: 'POST' });
    appState.selectedProject = await request('/api/projects/' + appState.selectedProject.project.id);
    appState.structureFeedbackOutline = result.id;
    renderProjectDetail(); $('#narrative-outline-area')?.scrollIntoView({ block: 'start' });
    showMessage('已另存大纲草稿，可调整标题、关键点与顺序。');
  } catch (error) { showMessage(error.message, true); }
  finally { if (button?.isConnected) setBusy(button, false); }
}
