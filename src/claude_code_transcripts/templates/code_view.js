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

// Get the URL for fetching code-data.json on gisthost/gistpreview
function getGistDataUrl() {
    // Check if we have a separate data gist (for large files)
    // window.DATA_GIST_ID is injected by inject_gist_preview_js when two-gist strategy is used
    if (window.DATA_GIST_ID) {
        return `https://gist.githubusercontent.com/raw/${window.DATA_GIST_ID}/code-data.json`;
    }

    // URL format: https://gisthost.github.io/?GIST_ID/code.html
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

    // Expose for testing
    window.codeViewData = { messagesData, fileData };

    // Windowed rendering state
    // We render a "window" of messages, not necessarily starting from 0
    const CHUNK_SIZE = 50;
    let windowStart = 0;      // First rendered message index
    let windowEnd = -1;       // Last rendered message index (-1 = none rendered)

    // For backwards compatibility
    function getRenderedCount() {
        return windowEnd - windowStart + 1;
    }

    // Find the user prompt that contains a given message index
    // Scans backwards to find a message with class "user" (non-continuation)
    function findUserPromptIndex(targetIndex) {
        for (let i = targetIndex; i >= 0; i--) {
            const msg = messagesData[i];
            // Check if this is a user message (not a continuation)
            if (msg.html && msg.html.includes('class="message user"') &&
                !msg.html.includes('class="continuation"')) {
                return i;
            }
        }
        return 0; // Fallback to start
    }

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

    // Build maps for range colors and message numbers
    // Uses pre-computed prompt_num and color_index from server
    function buildRangeMaps(blameRanges) {
        const colorMap = new Map();
        const msgNumMap = new Map();

        blameRanges.forEach((range, index) => {
            if (range.msg_id) {
                // Use pre-computed prompt_num from server
                if (range.prompt_num) {
                    msgNumMap.set(index, range.prompt_num);
                }

                // Use pre-computed color_index from server
                if (range.color_index !== null && range.color_index !== undefined) {
                    colorMap.set(index, rangeColors[range.color_index % rangeColors.length]);
                }
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
                        const range = blameRanges[parseInt(rangeIndex)];
                        if (range) {
                            updateLineHash(range.start);
                            // Scroll to the corresponding message in the transcript
                            if (range.msg_id) {
                                scrollToMessage(range.msg_id);
                            }
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

    // Append messages to the end of the transcript
    function appendMessages(startIdx, endIdx) {
        const transcriptContent = document.getElementById('transcript-content');
        const sentinel = document.getElementById('transcript-sentinel');
        let added = false;

        for (let i = startIdx; i <= endIdx && i < messagesData.length; i++) {
            if (i > windowEnd) {
                const msg = messagesData[i];
                const div = document.createElement('div');
                div.innerHTML = msg.html;
                while (div.firstChild) {
                    // Insert before the sentinel
                    transcriptContent.insertBefore(div.firstChild, sentinel);
                }
                windowEnd = i;
                added = true;
            }
        }

        if (added) {
            initTruncation(transcriptContent);
            formatTimestamps(transcriptContent);
        }
    }

    // Prepend messages to the beginning of the transcript
    function prependMessages(startIdx, endIdx) {
        const transcriptContent = document.getElementById('transcript-content');
        const topSentinel = document.getElementById('transcript-sentinel-top');
        let added = false;

        // Prepend in reverse order so they appear in correct sequence
        for (let i = endIdx; i >= startIdx && i >= 0; i--) {
            if (i < windowStart) {
                const msg = messagesData[i];
                const div = document.createElement('div');
                div.innerHTML = msg.html;
                // Insert all children after the top sentinel
                const children = Array.from(div.childNodes);
                const insertPoint = topSentinel ? topSentinel.nextSibling : transcriptContent.firstChild;
                children.forEach(child => {
                    transcriptContent.insertBefore(child, insertPoint);
                });
                windowStart = i;
                added = true;
            }
        }

        if (added) {
            initTruncation(transcriptContent);
            formatTimestamps(transcriptContent);
        }
    }

    // Clear and rebuild transcript starting from a specific index
    function teleportToMessage(targetIndex) {
        const transcriptContent = document.getElementById('transcript-content');
        const transcriptPanel = document.getElementById('transcript-panel');

        // Find the user prompt containing this message
        const promptStart = findUserPromptIndex(targetIndex);

        // Clear existing content (except sentinels - we'll recreate them)
        transcriptContent.innerHTML = '';

        // Add top sentinel for upward loading
        const topSentinel = document.createElement('div');
        topSentinel.id = 'transcript-sentinel-top';
        topSentinel.style.height = '1px';
        transcriptContent.appendChild(topSentinel);

        // Add bottom sentinel
        const bottomSentinel = document.createElement('div');
        bottomSentinel.id = 'transcript-sentinel';
        bottomSentinel.style.height = '1px';
        transcriptContent.appendChild(bottomSentinel);

        // Reset window state
        windowStart = promptStart;
        windowEnd = promptStart - 1;  // Will be updated by appendMessages

        // Render from user prompt up to AND INCLUDING the target message
        // This ensures the target is always in the DOM after teleporting
        const initialEnd = Math.max(
            Math.min(promptStart + CHUNK_SIZE - 1, messagesData.length - 1),
            targetIndex
        );
        appendMessages(promptStart, initialEnd);

        // Set up observers for the new sentinels
        setupScrollObservers();

        // Reset scroll position
        transcriptPanel.scrollTop = 0;
    }

    // Render messages down to targetIndex (extending window downward)
    function renderMessagesDownTo(targetIndex) {
        if (targetIndex <= windowEnd) return;
        appendMessages(windowEnd + 1, targetIndex);
    }

    // Render messages up to targetIndex (extending window upward)
    function renderMessagesUpTo(targetIndex) {
        if (targetIndex >= windowStart) return;
        prependMessages(targetIndex, windowStart - 1);
    }

    // Render next chunk downward (for lazy loading)
    function renderNextChunk() {
        const targetIndex = Math.min(windowEnd + CHUNK_SIZE, messagesData.length - 1);
        appendMessages(windowEnd + 1, targetIndex);
    }

    // Render previous chunk upward (for lazy loading)
    function renderPrevChunk() {
        if (windowStart <= 0) return;
        const targetIndex = Math.max(windowStart - CHUNK_SIZE, 0);
        prependMessages(targetIndex, windowStart - 1);
    }

    // Check if target message is within or near the current window
    function isNearCurrentWindow(msgIndex) {
        if (windowEnd < 0) return false;  // Nothing rendered yet
        const NEAR_THRESHOLD = CHUNK_SIZE * 2;
        return msgIndex >= windowStart - NEAR_THRESHOLD &&
               msgIndex <= windowEnd + NEAR_THRESHOLD;
    }

    // Scroll observers for lazy loading
    let topObserver = null;
    let bottomObserver = null;

    function setupScrollObservers() {
        // Clean up existing observers
        if (topObserver) topObserver.disconnect();
        if (bottomObserver) bottomObserver.disconnect();

        const transcriptPanel = document.getElementById('transcript-panel');

        // Bottom sentinel observer (load more below)
        const bottomSentinel = document.getElementById('transcript-sentinel');
        if (bottomSentinel) {
            bottomObserver = new IntersectionObserver((entries) => {
                if (entries[0].isIntersecting && windowEnd < messagesData.length - 1) {
                    renderNextChunk();
                }
            }, {
                root: transcriptPanel,
                rootMargin: '200px',
            });
            bottomObserver.observe(bottomSentinel);
        }

        // Top sentinel observer (load more above)
        const topSentinel = document.getElementById('transcript-sentinel-top');
        if (topSentinel) {
            topObserver = new IntersectionObserver((entries) => {
                if (entries[0].isIntersecting && windowStart > 0) {
                    // Save scroll position before prepending
                    const scrollTop = transcriptPanel.scrollTop;
                    const scrollHeight = transcriptPanel.scrollHeight;

                    renderPrevChunk();

                    // Adjust scroll position to maintain visual position
                    const newScrollHeight = transcriptPanel.scrollHeight;
                    const heightDiff = newScrollHeight - scrollHeight;
                    transcriptPanel.scrollTop = scrollTop + heightDiff;
                }
            }, {
                root: transcriptPanel,
                rootMargin: '200px',
            });
            topObserver.observe(topSentinel);
        }
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

    // Scroll to a message in the transcript
    // Uses teleportation for distant messages to avoid rendering thousands of DOM nodes
    // Always ensures the user prompt for the message is also loaded for context
    function scrollToMessage(msgId) {
        const transcriptContent = document.getElementById('transcript-content');
        const transcriptPanel = document.getElementById('transcript-panel');

        const msgIndex = msgIdToIndex.get(msgId);
        if (msgIndex === undefined) return;

        // Find the user prompt for this message - we always want it in the window
        const userPromptIndex = findUserPromptIndex(msgIndex);

        // Check if both user prompt and target message are in/near the window
        const targetNear = isNearCurrentWindow(msgIndex);
        const promptNear = isNearCurrentWindow(userPromptIndex);

        // Track if we teleported (need longer delay for layout)
        let didTeleport = false;

        // If either user prompt or target is far from window, teleport
        if (!targetNear || !promptNear) {
            teleportToMessage(msgIndex);
            didTeleport = true;
        } else {
            // Both are near the window - extend as needed
            // Ensure user prompt is loaded (extend upward if needed)
            if (userPromptIndex < windowStart) {
                renderMessagesUpTo(userPromptIndex);
            }
            // Ensure target message is loaded (extend downward if needed)
            if (msgIndex > windowEnd) {
                renderMessagesDownTo(msgIndex);
            }
        }

        // Helper to perform the scroll after DOM is ready
        const performScroll = () => {
            const message = transcriptContent.querySelector(`#${CSS.escape(msgId)}`);
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

                // Use instant scroll after teleport (jumping anyway), smooth otherwise
                transcriptPanel.scrollTo({
                    top: targetScroll,
                    behavior: didTeleport ? 'instant' : 'smooth'
                });

                // Re-enable pinned updates after scroll completes
                scrollTargetTimeout = setTimeout(() => {
                    isScrollingToTarget = false;
                    updatePinnedUserMessage();
                }, didTeleport ? 100 : 500);
            }
        };

        // After teleporting, wait for layout to complete before scrolling
        // Teleport adds many DOM elements - need time for browser to lay them out
        if (didTeleport) {
            // Use setTimeout to wait for layout, then requestAnimationFrame for paint
            setTimeout(() => {
                requestAnimationFrame(performScroll);
            }, 50);
        } else {
            requestAnimationFrame(performScroll);
        }
    }

    // Load file content
    // skipInitialScroll: if true, don't scroll to first blame range (caller will handle scroll)
    function loadFile(path, skipInitialScroll = false) {
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

            // Scroll to first blame range and align transcript (without highlighting)
            // Skip if caller will handle scroll (e.g., hash navigation to specific line)
            if (!skipInitialScroll) {
                const firstOpIndex = currentBlameRanges.findIndex(r => r.msg_id);
                if (firstOpIndex >= 0) {
                    const firstOpRange = currentBlameRanges[firstOpIndex];
                    scrollEditorToLine(firstOpRange.start);
                    // Scroll transcript to the corresponding message (no highlight on initial load)
                    if (firstOpRange.msg_id) {
                        scrollToMessage(firstOpRange.msg_id);
                    }
                }
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
            // Helper to scroll to line and select blame
            const scrollAndSelect = () => {
                scrollEditorToLine(lineNumber);
                // Find and highlight the range at this line
                if (currentBlameRanges.length > 0 && currentEditor) {
                    const rangeIndex = currentBlameRanges.findIndex(r =>
                        lineNumber >= r.start && lineNumber <= r.end
                    );
                    if (rangeIndex >= 0) {
                        const range = currentBlameRanges[rangeIndex];
                        highlightRange(rangeIndex, currentBlameRanges, currentEditor);
                        // Also scroll transcript to the corresponding message
                        if (range.msg_id) {
                            scrollToMessage(range.msg_id);
                        }
                    }
                }
            };

            // If we have a file path and it's different from current, load it
            if (filePath && filePath !== currentFilePath) {
                // Find and click the file in the tree
                const fileEl = document.querySelector(`.tree-file[data-path="${CSS.escape(filePath)}"]`);
                if (fileEl) {
                    document.querySelectorAll('.tree-file.selected').forEach(el => el.classList.remove('selected'));
                    fileEl.classList.add('selected');
                    // Skip initial scroll - scrollAndSelect will handle it
                    loadFile(filePath, true);
                    // Wait for file to load (loadFile uses setTimeout 10ms + rendering time)
                    setTimeout(scrollAndSelect, 100);
                }
                return true;
            } else if (filePath) {
                // Same file already loaded, just scroll
                requestAnimationFrame(scrollAndSelect);
                return true;
            } else if (lineNumber && !currentFilePath) {
                // Line number but no file loaded yet - let caller load first file
                // We'll handle the scroll after file loads
                return false;
            }
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

        // Helper to scroll and highlight the range
        const scrollAndHighlight = () => {
            scrollEditorToLine(range.start);
            if (currentEditor && currentBlameRanges.length > 0) {
                const idx = currentBlameRanges.findIndex(r => r.msg_id === msgId && r.start === range.start);
                if (idx >= 0) {
                    highlightRange(idx, currentBlameRanges, currentEditor);
                }
            }
            // Don't auto-scroll transcript - user is already viewing it
        };

        if (currentFilePath !== filePath) {
            // Skip initial scroll - scrollAndHighlight will handle it
            loadFile(filePath, true);
            // Wait for file to load (loadFile uses setTimeout 10ms + rendering time)
            setTimeout(scrollAndHighlight, 100);
        } else {
            requestAnimationFrame(scrollAndHighlight);
        }

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

    // Check URL hash for deep-linking FIRST
    // If hash specifies a file, we load that directly instead of the first file
    // This avoids race conditions between loading the first file and then the hash file
    const hashFileLoaded = navigateFromHash();

    // If no hash or hash didn't specify a file, load the first file
    if (!hashFileLoaded) {
        const firstFile = document.querySelector('.tree-file');
        if (firstFile) {
            firstFile.click();
            // If hash has just a line number (no file), apply it after first file loads
            if (window.location.hash.match(/^#L\d+$/)) {
                setTimeout(() => navigateFromHash(), 100);
            }
        }
    }

    // Mark initialization complete after a delay to let scrolling finish
    setTimeout(() => {
        isInitializing = false;
        updatePinnedUserMessage();
    }, 500);

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

    // Initialize transcript with windowed rendering
    // Add top sentinel for upward lazy loading
    const transcriptContentInit = document.getElementById('transcript-content');
    const topSentinelInit = document.createElement('div');
    topSentinelInit.id = 'transcript-sentinel-top';
    topSentinelInit.style.height = '1px';
    transcriptContentInit.insertBefore(topSentinelInit, transcriptContentInit.firstChild);

    // Render initial chunk of messages (starting from 0)
    windowStart = 0;
    windowEnd = -1;
    renderNextChunk();

    // Set up scroll observers for bi-directional lazy loading
    setupScrollObservers();

    // Sticky user message header
    const pinnedUserMessage = document.getElementById('pinned-user-message');
    const pinnedUserContent = pinnedUserMessage?.querySelector('.pinned-user-content');
    const pinnedUserLabel = pinnedUserMessage?.querySelector('.pinned-user-message-label');
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

    // Get the prompt number for any message from server-provided data
    function getPromptNumber(messageEl) {
        const msgId = messageEl.id;
        if (!msgId) return null;

        const msgIndex = msgIdToIndex.get(msgId);
        if (msgIndex === undefined) return null;

        // Every message has prompt_num set by the server
        return messagesData[msgIndex]?.prompt_num || null;
    }

    // Cache the pinned message height to avoid flashing when it's hidden
    let cachedPinnedHeight = 0;
    // Store pinned message ID separately (element reference may become stale after teleportation)
    let currentPinnedMsgId = null;

    function updatePinnedUserMessage() {
        if (!pinnedUserMessage || !transcriptContent || !transcriptPanel) return;
        if (isInitializing || isScrollingToTarget) return;  // Skip during scrolling to avoid repeated updates

        const userMessages = transcriptContent.querySelectorAll('.message.user:not(.continuation)');
        if (userMessages.length === 0) {
            pinnedUserMessage.style.display = 'none';
            currentPinnedMessage = null;
            currentPinnedMsgId = null;
            return;
        }

        const panelRect = transcriptPanel.getBoundingClientRect();
        const headerHeight = transcriptPanel.querySelector('h3')?.offsetHeight || 0;

        // Use cached height if pinned is hidden, otherwise update cache
        if (pinnedUserMessage.style.display !== 'none') {
            cachedPinnedHeight = pinnedUserMessage.offsetHeight || cachedPinnedHeight;
        }
        // Use a minimum height estimate if we've never measured it
        const pinnedHeight = cachedPinnedHeight || 40;

        // Threshold for when a message is considered "scrolled past"
        const pinnedAreaBottom = panelRect.top + headerHeight + pinnedHeight;

        let messageToPin = null;
        let nextUserMessage = null;

        for (const msg of userMessages) {
            const msgRect = msg.getBoundingClientRect();
            // A message should be pinned if its bottom is above the pinned area
            if (msgRect.bottom < pinnedAreaBottom) {
                messageToPin = msg;
            } else {
                // This is the first user message that's visible
                nextUserMessage = msg;
                break;
            }
        }

        // Hide pinned if the next user message is entering the pinned area
        // Use a small buffer to prevent flashing at the boundary
        if (nextUserMessage) {
            const nextRect = nextUserMessage.getBoundingClientRect();
            if (nextRect.top < pinnedAreaBottom) {
                // Next user message is in the pinned area - hide the pinned
                messageToPin = null;
            }
        }

        if (messageToPin && messageToPin !== currentPinnedMessage) {
            currentPinnedMessage = messageToPin;
            currentPinnedMsgId = messageToPin.id;
            const promptNum = getPromptNumber(messageToPin);
            // Update label with prompt number
            if (pinnedUserLabel) {
                pinnedUserLabel.textContent = promptNum ? `User Prompt #${promptNum}` : 'User Prompt';
            }
            pinnedUserContent.textContent = extractUserMessageText(messageToPin);
            pinnedUserMessage.style.display = 'block';
            // Use message ID to look up element on click (element may be stale after teleportation)
            pinnedUserMessage.onclick = () => {
                if (currentPinnedMsgId) {
                    const msgEl = transcriptContent.querySelector(`#${CSS.escape(currentPinnedMsgId)}`);
                    if (msgEl) {
                        msgEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    } else {
                        // Element not in DOM (teleported away) - use scrollToMessage to bring it back
                        scrollToMessage(currentPinnedMsgId);
                    }
                }
            };
        } else if (!messageToPin) {
            pinnedUserMessage.style.display = 'none';
            currentPinnedMessage = null;
            currentPinnedMsgId = null;
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
