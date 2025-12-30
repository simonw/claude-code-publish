// CodeMirror 6 imports from CDN
import {EditorView, lineNumbers, gutter, GutterMarker, Decoration, ViewPlugin, WidgetType} from 'https://esm.sh/@codemirror/view@6';
import {EditorState, StateField, StateEffect} from 'https://esm.sh/@codemirror/state@6';

// Widget to show user message number at end of line
class MessageNumberWidget extends WidgetType {
    constructor(msgNum) {
        super();
        this.msgNum = msgNum;
    }
    toDOM() {
        const span = document.createElement('span');
        span.className = 'blame-msg-num';
        span.textContent = `#${this.msgNum}`;
        return span;
    }
    eq(other) {
        return this.msgNum === other.msgNum;
    }
}
import {syntaxHighlighting, defaultHighlightStyle} from 'https://esm.sh/@codemirror/language@6';
import {javascript} from 'https://esm.sh/@codemirror/lang-javascript@6';
import {python} from 'https://esm.sh/@codemirror/lang-python@6';
import {html} from 'https://esm.sh/@codemirror/lang-html@6';
import {css} from 'https://esm.sh/@codemirror/lang-css@6';
import {json} from 'https://esm.sh/@codemirror/lang-json@6';
import {markdown} from 'https://esm.sh/@codemirror/lang-markdown@6';

// Format timestamps in local timezone with nice format
function formatTimestamp(date) {
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    const isYesterday = date.toDateString() === yesterday.toDateString();
    const isThisYear = date.getFullYear() === now.getFullYear();

    const timeStr = date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

    if (isToday) {
        return timeStr;
    } else if (isYesterday) {
        return 'Yesterday ' + timeStr;
    } else if (isThisYear) {
        return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + timeStr;
    } else {
        return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) + ' ' + timeStr;
    }
}

function formatTimestamps(container) {
    container.querySelectorAll('time[data-timestamp]').forEach(function(el) {
        const timestamp = el.getAttribute('data-timestamp');
        const date = new Date(timestamp);
        el.textContent = formatTimestamp(date);
        el.title = date.toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'long' });
    });
}

// Get the URL for fetching code-data.json on gistpreview
function getGistDataUrl() {
    // Check if we have a separate data gist (for large files)
    // window.DATA_GIST_ID is injected by inject_gist_preview_js when two-gist strategy is used
    if (window.DATA_GIST_ID) {
        return `https://gist.githubusercontent.com/raw/${window.DATA_GIST_ID}/code-data.json`;
    }

    // URL format: https://gistpreview.github.io/?GIST_ID/code.html
    const match = window.location.search.match(/^\?([^/]+)/);
    if (match) {
        const gistId = match[1];
        // Use raw gist URL (no API rate limits)
        return `https://gist.githubusercontent.com/raw/${gistId}/code-data.json`;
    }
    return null;
}

// Show loading state
function showLoading() {
    const codeContent = document.getElementById('code-content');
    if (codeContent) {
        codeContent.innerHTML = '<p style="padding: 16px; color: #888;">Loading code data...</p>';
    }
}

// Show error state
function showError(message) {
    const codeContent = document.getElementById('code-content');
    if (codeContent) {
        codeContent.innerHTML = `<p style="padding: 16px; color: #f44;">Error: ${message}</p>`;
    }
}

// Palette of colors for blame ranges
const rangeColors = [
    'rgba(66, 165, 245, 0.15)',   // blue
    'rgba(102, 187, 106, 0.15)',  // green
    'rgba(255, 167, 38, 0.15)',   // orange
    'rgba(171, 71, 188, 0.15)',   // purple
    'rgba(239, 83, 80, 0.15)',    // red
    'rgba(38, 198, 218, 0.15)',   // cyan
];

// State effect for updating active range
const setActiveRange = StateEffect.define();

// State field for active range highlighting
const activeRangeField = StateField.define({
    create() { return Decoration.none; },
    update(decorations, tr) {
        for (let e of tr.effects) {
            if (e.is(setActiveRange)) {
                const {rangeIndex, blameRanges, doc} = e.value;
                if (rangeIndex < 0 || rangeIndex >= blameRanges.length) {
                    return Decoration.none;
                }
                const range = blameRanges[rangeIndex];
                const decs = [];
                for (let line = range.start; line <= range.end; line++) {
                    if (line <= doc.lines) {
                        const lineStart = doc.line(line).from;
                        decs.push(
                            Decoration.line({
                                class: 'cm-active-range'
                            }).range(lineStart)
                        );
                    }
                }
                return Decoration.set(decs, true);
            }
        }
        return decorations;
    },
    provide: f => EditorView.decorations.from(f)
});

// Main initialization - uses embedded data or fetches from gist
async function init() {
    let data;

    // Always show loading on init - parsing large embedded JSON takes time
    showLoading();

    // Check for embedded data first (works with local file:// access)
    if (window.CODE_DATA) {
        // Use setTimeout to allow the loading message to render before heavy processing
        await new Promise(resolve => setTimeout(resolve, 0));
        data = window.CODE_DATA;
    } else {
        // No embedded data - must be gist version, fetch from raw URL
        showLoading();
        const dataUrl = getGistDataUrl();
        if (!dataUrl) {
            showError('No data available. If viewing locally, the file may be corrupted.');
            return;
        }
        try {
            const response = await fetch(dataUrl);
            if (!response.ok) {
                throw new Error(`Failed to fetch data: ${response.status} ${response.statusText}`);
            }
            data = await response.json();
        } catch (err) {
            showError(err.message);
            console.error('Failed to load code data:', err);
            return;
        }
    }

    const fileData = data.fileData;
    const messagesData = data.messagesData;

    // Chunked rendering state
    const CHUNK_SIZE = 50;
    let renderedCount = 0;

    // Build ID-to-index map for fast lookup
    const msgIdToIndex = new Map();
    messagesData.forEach((msg, index) => {
        if (msg.id) {
            msgIdToIndex.set(msg.id, index);
        }
    });

    // Build msg_id to file/range map for navigating from transcript to code
    const msgIdToBlame = new Map();
    Object.entries(fileData).forEach(([filePath, fileInfo]) => {
        (fileInfo.blame_ranges || []).forEach((range, rangeIndex) => {
            if (range.msg_id) {
                if (!msgIdToBlame.has(range.msg_id)) {
                    msgIdToBlame.set(range.msg_id, { filePath, range, rangeIndex });
                }
            }
        });
    });

    // Build sorted list of blame operations by message index
    const sortedBlameOps = [];
    msgIdToBlame.forEach((blameInfo, msgId) => {
        const msgIndex = msgIdToIndex.get(msgId);
        if (msgIndex !== undefined) {
            sortedBlameOps.push({ msgId, msgIndex, ...blameInfo });
        }
    });
    sortedBlameOps.sort((a, b) => a.msgIndex - b.msgIndex);

    // Find the first blame operation at or after a given message index
    function findNextBlameOp(msgIndex) {
        for (const op of sortedBlameOps) {
            if (op.msgIndex >= msgIndex) {
                return op;
            }
        }
        return null;
    }

    // Current state
    let currentEditor = null;
    let currentFilePath = null;
    let currentBlameRanges = [];
    let isInitializing = true;  // Skip pinned message updates during initial load
    let isScrollingToTarget = false;  // Skip pinned updates during programmatic scrolls
    let scrollTargetTimeout = null;

    // Tooltip element for blame hover
    let blameTooltip = null;

    function createBlameTooltip() {
        const tooltip = document.createElement('div');
        tooltip.className = 'blame-tooltip';
        tooltip.style.display = 'none';
        document.body.appendChild(tooltip);
        return tooltip;
    }

    function showBlameTooltip(event, html) {
        if (!blameTooltip) {
            blameTooltip = createBlameTooltip();
        }
        if (!html) return;

        const codePanel = document.getElementById('code-panel');
        if (codePanel) {
            const codePanelWidth = codePanel.offsetWidth;
            const tooltipWidth = Math.min(Math.max(codePanelWidth * 0.75, 300), 800);
            blameTooltip.style.maxWidth = tooltipWidth + 'px';
        }

        blameTooltip.innerHTML = html;
        formatTimestamps(blameTooltip);
        blameTooltip.style.display = 'block';

        const padding = 10;
        let x = event.clientX + padding;
        let y = event.clientY + padding;

        const rect = blameTooltip.getBoundingClientRect();
        const maxX = window.innerWidth - rect.width - padding;
        const maxY = window.innerHeight - rect.height - padding;

        if (x > maxX) x = event.clientX - rect.width - padding;
        if (y > maxY) {
            const yAbove = event.clientY - rect.height - padding;
            if (yAbove >= 0) {
                y = yAbove;
            }
        }

        blameTooltip.style.left = x + 'px';
        blameTooltip.style.top = y + 'px';
    }

    function hideBlameTooltip() {
        if (blameTooltip) {
            blameTooltip.style.display = 'none';
        }
    }

    // Extract prompt number from user_html
    function extractPromptNum(userHtml) {
        if (!userHtml) return null;
        const match = userHtml.match(/index-item-number">#(\d+)</);
        return match ? parseInt(match[1]) : null;
    }

    // Build maps for range colors and message numbers
    function buildRangeMaps(blameRanges) {
        const colorMap = new Map();
        const msgNumMap = new Map();
        const contextToColor = new Map();
        let colorIndex = 0;

        blameRanges.forEach((range, index) => {
            if (range.msg_id) {
                const promptNum = extractPromptNum(range.user_html);
                if (promptNum) {
                    msgNumMap.set(index, promptNum);
                }

                const contextId = range.context_msg_id || range.msg_id;
                if (!contextToColor.has(contextId)) {
                    contextToColor.set(contextId, rangeColors[colorIndex % rangeColors.length]);
                    colorIndex++;
                }
                colorMap.set(index, contextToColor.get(contextId));
            }
        });
        return { colorMap, msgNumMap };
    }

    // Language detection based on file extension
    function getLanguageExtension(filePath) {
        const ext = filePath.split('.').pop().toLowerCase();
        const langMap = {
            'js': javascript(),
            'jsx': javascript({jsx: true}),
            'ts': javascript({typescript: true}),
            'tsx': javascript({jsx: true, typescript: true}),
            'mjs': javascript(),
            'cjs': javascript(),
            'py': python(),
            'html': html(),
            'htm': html(),
            'css': css(),
            'json': json(),
            'md': markdown(),
            'markdown': markdown(),
        };
        return langMap[ext] || [];
    }

    // Create line decorations for blame ranges
    function createRangeDecorations(blameRanges, doc, colorMap, msgNumMap) {
        const decorations = [];

        blameRanges.forEach((range, index) => {
            const color = colorMap.get(index);
            if (!color) return;

            for (let line = range.start; line <= range.end; line++) {
                if (line <= doc.lines) {
                    const lineInfo = doc.line(line);
                    const lineStart = lineInfo.from;

                    decorations.push(
                        Decoration.line({
                            attributes: {
                                style: `background-color: ${color}`,
                                'data-range-index': index.toString(),
                                'data-msg-id': range.msg_id,
                            }
                        }).range(lineStart)
                    );

                    if (line === range.start) {
                        const msgNum = msgNumMap.get(index);
                        if (msgNum) {
                            decorations.push(
                                Decoration.widget({
                                    widget: new MessageNumberWidget(msgNum),
                                    side: 1,
                                }).range(lineInfo.to)
                            );
                        }
                    }
                }
            }
        });

        return Decoration.set(decorations, true);
    }

    // Create the scrollbar minimap
    function createMinimap(container, blameRanges, totalLines, editor, colorMap) {
        const existing = container.querySelector('.blame-minimap');
        if (existing) existing.remove();

        if (colorMap.size === 0 || totalLines === 0) return null;

        // Check if scrolling is needed - if not, don't show minimap
        const editorContainer = container.querySelector('.editor-container');
        const scrollElement = editorContainer?.querySelector('.cm-scroller');
        if (scrollElement) {
            const needsScroll = scrollElement.scrollHeight > scrollElement.clientHeight;
            if (!needsScroll) return null;
        }

        const minimap = document.createElement('div');
        minimap.className = 'blame-minimap';

        blameRanges.forEach((range, index) => {
            const color = colorMap.get(index);
            if (!color) return;

            const startPercent = ((range.start - 1) / totalLines) * 100;
            const endPercent = (range.end / totalLines) * 100;
            const height = Math.max(endPercent - startPercent, 0.5);

            const marker = document.createElement('div');
            marker.className = 'minimap-marker';
            marker.style.top = startPercent + '%';
            marker.style.height = height + '%';
            marker.style.backgroundColor = color.replace('0.15', '0.6');
            marker.dataset.rangeIndex = index;
            marker.dataset.line = range.start;
            marker.title = `Lines ${range.start}-${range.end}`;

            marker.addEventListener('click', () => {
                const doc = editor.state.doc;
                if (range.start <= doc.lines) {
                    const lineInfo = doc.line(range.start);
                    editor.dispatch({
                        effects: EditorView.scrollIntoView(lineInfo.from, { y: 'center' })
                    });
                    highlightRange(index, blameRanges, editor);
                    if (range.msg_id) {
                        scrollToMessage(range.msg_id);
                    }
                }
            });

            minimap.appendChild(marker);
        });

        container.appendChild(minimap);
        return minimap;
    }

    // Create editor for a file
    function createEditor(container, content, blameRanges, filePath) {
        container.innerHTML = '';

        const wrapper = document.createElement('div');
        wrapper.className = 'editor-wrapper';
        container.appendChild(wrapper);

        const editorContainer = document.createElement('div');
        editorContainer.className = 'editor-container';
        wrapper.appendChild(editorContainer);

        const doc = EditorState.create({doc: content}).doc;
        const { colorMap, msgNumMap } = buildRangeMaps(blameRanges);
        const rangeDecorations = createRangeDecorations(blameRanges, doc, colorMap, msgNumMap);

        const rangeDecorationsField = StateField.define({
            create() { return rangeDecorations; },
            update(decorations) { return decorations; },
            provide: f => EditorView.decorations.from(f)
        });

        const clickHandler = EditorView.domEventHandlers({
            click: (event, view) => {
                const target = event.target;
                if (target.closest('.cm-line')) {
                    const line = target.closest('.cm-line');
                    const rangeIndex = line.getAttribute('data-range-index');
                    if (rangeIndex !== null) {
                        highlightRange(parseInt(rangeIndex), blameRanges, view);
                        // Update URL hash for deep-linking (don't scroll transcript -
                        // that would render thousands of messages and hang the browser)
                        const range = blameRanges[parseInt(rangeIndex)];
                        if (range) {
                            updateLineHash(range.start);
                        }
                    }
                }
            },
            mouseover: (event, view) => {
                const target = event.target;
                const line = target.closest('.cm-line');
                if (line) {
                    const rangeIndex = line.getAttribute('data-range-index');
                    if (rangeIndex !== null) {
                        const range = blameRanges[parseInt(rangeIndex)];
                        if (range && range.user_html) {
                            showBlameTooltip(event, range.user_html);
                        }
                    }
                }
            },
            mouseout: (event, view) => {
                const target = event.target;
                const line = target.closest('.cm-line');
                if (line) {
                    hideBlameTooltip();
                }
            },
            mousemove: (event, view) => {
                const target = event.target;
                const line = target.closest('.cm-line');
                if (line && line.getAttribute('data-range-index') !== null) {
                    const rangeIndex = parseInt(line.getAttribute('data-range-index'));
                    const range = blameRanges[rangeIndex];
                    if (range && range.user_html && blameTooltip && blameTooltip.style.display !== 'none') {
                        showBlameTooltip(event, range.user_html);
                    }
                }
            }
        });

        const extensions = [
            lineNumbers(),
            EditorView.editable.of(false),
            EditorView.lineWrapping,
            syntaxHighlighting(defaultHighlightStyle),
            getLanguageExtension(filePath),
            rangeDecorationsField,
            activeRangeField,
            clickHandler,
        ];

        const state = EditorState.create({
            doc: content,
            extensions: extensions,
        });

        currentEditor = new EditorView({
            state,
            parent: editorContainer,
        });

        createMinimap(wrapper, blameRanges, doc.lines, currentEditor, colorMap);

        return currentEditor;
    }

    // Highlight a specific range in the editor
    function highlightRange(rangeIndex, blameRanges, view) {
        view.dispatch({
            effects: setActiveRange.of({
                rangeIndex,
                blameRanges,
                doc: view.state.doc
            })
        });
    }

    // Initialize truncation for elements within a container
    function initTruncation(container) {
        container.querySelectorAll('.truncatable:not(.truncation-initialized)').forEach(function(wrapper) {
            wrapper.classList.add('truncation-initialized');
            const content = wrapper.querySelector('.truncatable-content');
            const btn = wrapper.querySelector('.expand-btn');
            if (content && content.scrollHeight > 250) {
                wrapper.classList.add('truncated');
                if (btn) {
                    btn.addEventListener('click', function() {
                        if (wrapper.classList.contains('truncated')) {
                            wrapper.classList.remove('truncated');
                            wrapper.classList.add('expanded');
                            btn.textContent = 'Show less';
                        } else {
                            wrapper.classList.remove('expanded');
                            wrapper.classList.add('truncated');
                            btn.textContent = 'Show more';
                        }
                    });
                }
            }
        });
    }

    // Render a single chunk of messages (synchronous)
    function renderChunk(startIdx, endIdx) {
        const transcriptContent = document.getElementById('transcript-content');
        const initialCount = renderedCount;

        while (renderedCount <= endIdx && renderedCount < messagesData.length) {
            const msg = messagesData[renderedCount];
            const div = document.createElement('div');
            div.innerHTML = msg.html;
            while (div.firstChild) {
                transcriptContent.appendChild(div.firstChild);
            }
            renderedCount++;
        }

        if (renderedCount > initialCount) {
            initTruncation(transcriptContent);
            formatTimestamps(transcriptContent);
        }
    }

    // Render messages up to targetIndex synchronously (for small jumps)
    function renderMessagesUpTo(targetIndex) {
        if (targetIndex < renderedCount) return;
        renderChunk(renderedCount, targetIndex);
    }

    // Render messages progressively with UI breaks (for large jumps)
    // Returns a promise that resolves when rendering is complete
    function renderMessagesProgressively(targetIndex) {
        return new Promise((resolve) => {
            if (targetIndex < renderedCount) {
                resolve();
                return;
            }

            const PROGRESSIVE_CHUNK_SIZE = 200; // Larger chunks for speed, with breaks between

            function renderNextBatch() {
                const batchEnd = Math.min(renderedCount + PROGRESSIVE_CHUNK_SIZE - 1, targetIndex);
                renderChunk(renderedCount, batchEnd);

                if (renderedCount <= targetIndex && renderedCount < messagesData.length) {
                    // Use setTimeout to yield to the browser for UI updates
                    setTimeout(renderNextBatch, 0);
                } else {
                    resolve();
                }
            }

            renderNextBatch();
        });
    }

    function renderNextChunk() {
        const targetIndex = Math.min(renderedCount + CHUNK_SIZE - 1, messagesData.length - 1);
        renderMessagesUpTo(targetIndex);
    }

    // Calculate sticky header offset
    function getStickyHeaderOffset() {
        const panel = document.getElementById('transcript-panel');
        const h3 = panel?.querySelector('h3');
        const pinnedMsg = document.getElementById('pinned-user-message');

        let offset = 0;
        if (h3) offset += h3.offsetHeight;
        if (pinnedMsg && pinnedMsg.style.display !== 'none') {
            offset += pinnedMsg.offsetHeight;
        }
        return offset + 8;
    }

    // Scroll to a message in the transcript (async for progressive rendering)
    async function scrollToMessage(msgId) {
        const transcriptContent = document.getElementById('transcript-content');
        const transcriptPanel = document.getElementById('transcript-panel');

        const msgIndex = msgIdToIndex.get(msgId);
        if (msgIndex !== undefined && msgIndex >= renderedCount) {
            // Use progressive rendering for large jumps to keep UI responsive
            await renderMessagesProgressively(msgIndex);
        }

        const message = transcriptContent.querySelector(`#${msgId}`);
        if (message) {
            transcriptContent.querySelectorAll('.message.highlighted').forEach(el => {
                el.classList.remove('highlighted');
            });
            message.classList.add('highlighted');

            const stickyOffset = getStickyHeaderOffset();
            const messageTop = message.offsetTop;
            const targetScroll = messageTop - stickyOffset;

            // Suppress pinned message updates during scroll
            isScrollingToTarget = true;
            if (scrollTargetTimeout) clearTimeout(scrollTargetTimeout);

            transcriptPanel.scrollTo({
                top: targetScroll,
                behavior: 'smooth'
            });

            // Re-enable pinned updates after scroll completes
            scrollTargetTimeout = setTimeout(() => {
                isScrollingToTarget = false;
                updatePinnedUserMessage();
            }, 500);
        }
    }

    // Load file content
    function loadFile(path) {
        currentFilePath = path;

        const codeContent = document.getElementById('code-content');
        const currentFilePathEl = document.getElementById('current-file-path');

        currentFilePathEl.textContent = path;

        const fileInfo = fileData[path];
        if (!fileInfo) {
            codeContent.innerHTML = '<p style="padding: 16px;">File not found</p>';
            return;
        }

        // Always show loading indicator - gives visual feedback during file switch
        codeContent.innerHTML = '<div class="initial-loading"><p>Loading file...</p></div>';

        // Use setTimeout to ensure loading message renders before heavy work
        setTimeout(() => {
            const content = fileInfo.content || '';
            currentBlameRanges = fileInfo.blame_ranges || [];
            createEditor(codeContent, content, currentBlameRanges, path);

            // Auto-select the first blame range: highlight in editor only
            // Don't auto-scroll transcript - that would render thousands of messages
            // User can click the blame range to navigate to the message
            const firstOpIndex = currentBlameRanges.findIndex(r => r.msg_id);
            if (firstOpIndex >= 0) {
                const firstOpRange = currentBlameRanges[firstOpIndex];
                scrollEditorToLine(firstOpRange.start);
                highlightRange(firstOpIndex, currentBlameRanges, currentEditor);
            }
        }, 10);
    }

    // Scroll editor to a line
    function scrollEditorToLine(lineNumber) {
        if (!currentEditor) return;
        const doc = currentEditor.state.doc;
        if (lineNumber < 1 || lineNumber > doc.lines) return;

        const line = doc.line(lineNumber);
        currentEditor.dispatch({
            effects: EditorView.scrollIntoView(line.from, { y: 'center' })
        });
    }

    // Update URL hash for deep-linking to a line
    function updateLineHash(lineNumber) {
        if (!currentFilePath) return;
        // Use format: #path/to/file:L{number}
        const hash = `${encodeURIComponent(currentFilePath)}:L${lineNumber}`;
        history.replaceState(null, '', `#${hash}`);
    }

    // Parse URL hash and navigate to file/line
    // Supports formats: #L5, #path/to/file:L5, #path%2Fto%2Ffile:L5
    function navigateFromHash() {
        const hash = window.location.hash.slice(1); // Remove leading #
        if (!hash) return false;

        let filePath = null;
        let lineNumber = null;

        // Check for file:L{number} format
        const fileLineMatch = hash.match(/^(.+):L(\d+)$/);
        if (fileLineMatch) {
            filePath = decodeURIComponent(fileLineMatch[1]);
            lineNumber = parseInt(fileLineMatch[2]);
        } else {
            // Check for just L{number} format (uses current file)
            const lineMatch = hash.match(/^L(\d+)$/);
            if (lineMatch) {
                lineNumber = parseInt(lineMatch[1]);
                filePath = currentFilePath; // Use current file
            }
        }

        if (lineNumber) {
            // If we have a file path and it's different from current, load it
            if (filePath && filePath !== currentFilePath) {
                // Find and click the file in the tree
                const fileEl = document.querySelector(`.tree-file[data-path="${CSS.escape(filePath)}"]`);
                if (fileEl) {
                    document.querySelectorAll('.tree-file.selected').forEach(el => el.classList.remove('selected'));
                    fileEl.classList.add('selected');
                    loadFile(filePath);
                }
            }

            // Wait for editor to be ready, then scroll to line
            requestAnimationFrame(() => {
                scrollEditorToLine(lineNumber);
                // Find and highlight the range at this line
                if (currentBlameRanges.length > 0 && currentEditor) {
                    const rangeIndex = currentBlameRanges.findIndex(r =>
                        lineNumber >= r.start && lineNumber <= r.end
                    );
                    if (rangeIndex >= 0) {
                        highlightRange(rangeIndex, currentBlameRanges, currentEditor);
                    }
                }
            });
            return true;
        }
        return false;
    }

    // Navigate from message to code
    function navigateToBlame(msgId) {
        const blameInfo = msgIdToBlame.get(msgId);
        if (!blameInfo) return false;

        const { filePath, range, rangeIndex } = blameInfo;

        const fileEl = document.querySelector(`.tree-file[data-path="${CSS.escape(filePath)}"]`);
        if (fileEl) {
            let parent = fileEl.parentElement;
            while (parent && parent.id !== 'file-tree') {
                if (parent.classList.contains('tree-dir') && !parent.classList.contains('open')) {
                    parent.classList.add('open');
                }
                parent = parent.parentElement;
            }

            document.querySelectorAll('.tree-file.selected').forEach(el => el.classList.remove('selected'));
            fileEl.classList.add('selected');
        }

        if (currentFilePath !== filePath) {
            loadFile(filePath);
        }

        requestAnimationFrame(() => {
            scrollEditorToLine(range.start);
            if (currentEditor && currentBlameRanges.length > 0) {
                const idx = currentBlameRanges.findIndex(r => r.msg_id === msgId && r.start === range.start);
                if (idx >= 0) {
                    highlightRange(idx, currentBlameRanges, currentEditor);
                }
            }
            // Don't auto-scroll transcript - user is already viewing it and
            // scrolling to a distant message would render thousands of DOM nodes
        });

        return true;
    }

    // Set up file tree interaction
    document.getElementById('file-tree').addEventListener('click', (e) => {
        const dir = e.target.closest('.tree-dir');
        if (dir && (e.target.classList.contains('tree-toggle') || e.target.classList.contains('tree-dir-name'))) {
            dir.classList.toggle('open');
            return;
        }

        const file = e.target.closest('.tree-file');
        if (file) {
            document.querySelectorAll('.tree-file.selected').forEach((el) => {
                el.classList.remove('selected');
            });
            file.classList.add('selected');
            loadFile(file.dataset.path);
        }
    });

    // Auto-select first file, or navigate from hash if present
    const firstFile = document.querySelector('.tree-file');
    if (firstFile) {
        firstFile.click();
    }

    // Check URL hash for deep-linking (after first file loads)
    requestAnimationFrame(() => {
        navigateFromHash();
        // Mark initialization complete after a delay to let scrolling finish
        setTimeout(() => {
            isInitializing = false;
            updatePinnedUserMessage();
        }, 500);
    });

    // Handle hash changes (browser back/forward)
    window.addEventListener('hashchange', () => {
        navigateFromHash();
    });

    // Resizable panels
    function initResize() {
        const fileTreePanel = document.getElementById('file-tree-panel');
        const transcriptPanel = document.getElementById('transcript-panel');
        const resizeLeft = document.getElementById('resize-left');
        const resizeRight = document.getElementById('resize-right');

        let isResizing = false;
        let currentHandle = null;
        let startX = 0;
        let startWidthLeft = 0;
        let startWidthRight = 0;

        function startResize(e, handle) {
            isResizing = true;
            currentHandle = handle;
            startX = e.clientX;
            handle.classList.add('dragging');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';

            if (handle === resizeLeft) {
                startWidthLeft = fileTreePanel.offsetWidth;
            } else {
                startWidthRight = transcriptPanel.offsetWidth;
            }

            e.preventDefault();
        }

        function doResize(e) {
            if (!isResizing) return;

            const dx = e.clientX - startX;

            if (currentHandle === resizeLeft) {
                const newWidth = Math.max(200, Math.min(500, startWidthLeft + dx));
                fileTreePanel.style.width = newWidth + 'px';
            } else {
                const newWidth = Math.max(280, Math.min(700, startWidthRight - dx));
                transcriptPanel.style.width = newWidth + 'px';
            }
        }

        function stopResize() {
            if (!isResizing) return;
            isResizing = false;
            if (currentHandle) {
                currentHandle.classList.remove('dragging');
            }
            currentHandle = null;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }

        resizeLeft.addEventListener('mousedown', (e) => startResize(e, resizeLeft));
        resizeRight.addEventListener('mousedown', (e) => startResize(e, resizeRight));
        document.addEventListener('mousemove', doResize);
        document.addEventListener('mouseup', stopResize);
    }

    initResize();

    // File tree collapse/expand
    const collapseBtn = document.getElementById('collapse-file-tree');
    const fileTreePanel = document.getElementById('file-tree-panel');
    const resizeLeftHandle = document.getElementById('resize-left');

    if (collapseBtn && fileTreePanel) {
        collapseBtn.addEventListener('click', () => {
            fileTreePanel.classList.toggle('collapsed');
            if (resizeLeftHandle) {
                resizeLeftHandle.style.display = fileTreePanel.classList.contains('collapsed') ? 'none' : '';
            }
            collapseBtn.title = fileTreePanel.classList.contains('collapsed') ? 'Expand file tree' : 'Collapse file tree';
        });
    }

    // Render initial chunk of messages
    renderNextChunk();

    // Set up IntersectionObserver for lazy loading
    const sentinel = document.getElementById('transcript-sentinel');
    if (sentinel) {
        const observer = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && renderedCount < messagesData.length) {
                renderNextChunk();
            }
        }, {
            root: document.getElementById('transcript-panel'),
            rootMargin: '200px',
        });
        observer.observe(sentinel);
    }

    // Sticky user message header
    const pinnedUserMessage = document.getElementById('pinned-user-message');
    const pinnedUserContent = pinnedUserMessage?.querySelector('.pinned-user-content');
    const transcriptPanel = document.getElementById('transcript-panel');
    const transcriptContent = document.getElementById('transcript-content');
    let currentPinnedMessage = null;

    function extractUserMessageText(messageEl) {
        const contentEl = messageEl.querySelector('.message-content');
        if (!contentEl) return '';

        let text = contentEl.textContent.trim();
        if (text.length > 150) {
            text = text.substring(0, 150) + '...';
        }
        return text;
    }

    function updatePinnedUserMessage() {
        if (!pinnedUserMessage || !transcriptContent || !transcriptPanel) return;
        if (isInitializing || isScrollingToTarget) return;  // Skip during scrolling to avoid repeated updates

        const userMessages = transcriptContent.querySelectorAll('.message.user:not(.continuation *)');
        if (userMessages.length === 0) {
            pinnedUserMessage.style.display = 'none';
            currentPinnedMessage = null;
            return;
        }

        const panelRect = transcriptPanel.getBoundingClientRect();
        const headerHeight = transcriptPanel.querySelector('h3')?.offsetHeight || 0;
        const pinnedHeight = pinnedUserMessage.offsetHeight || 0;
        const topThreshold = panelRect.top + headerHeight + pinnedHeight + 10;

        let messageToPin = null;
        for (const msg of userMessages) {
            if (msg.getBoundingClientRect().bottom < topThreshold) {
                messageToPin = msg;
            } else {
                break;
            }
        }

        if (messageToPin && messageToPin !== currentPinnedMessage) {
            currentPinnedMessage = messageToPin;
            pinnedUserContent.textContent = extractUserMessageText(messageToPin);
            pinnedUserMessage.style.display = 'block';
            pinnedUserMessage.onclick = () => {
                messageToPin.scrollIntoView({ behavior: 'smooth', block: 'start' });
            };
        } else if (!messageToPin) {
            pinnedUserMessage.style.display = 'none';
            currentPinnedMessage = null;
        }
    }

    // Throttle scroll handler
    let scrollTimeout = null;
    transcriptPanel?.addEventListener('scroll', () => {
        if (scrollTimeout) return;
        scrollTimeout = setTimeout(() => {
            updatePinnedUserMessage();
            scrollTimeout = null;
        }, 16);
    });

    setTimeout(updatePinnedUserMessage, 100);

    // Click handler for transcript messages
    transcriptContent?.addEventListener('click', (e) => {
        const messageEl = e.target.closest('.message');
        if (!messageEl) return;

        const msgId = messageEl.id;
        if (!msgId) return;

        const msgIndex = msgIdToIndex.get(msgId);
        if (msgIndex === undefined) return;

        const nextOp = findNextBlameOp(msgIndex);
        if (nextOp) {
            navigateToBlame(nextOp.msgId);
        }
    });
}

// Start initialization
init();
