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

// File data embedded in page
const fileData = {{ file_data_json|safe }};

// Transcript messages data for chunked rendering
const messagesData = {{ messages_json|safe }};
const CHUNK_SIZE = 50;
let renderedCount = 0;
const msgIdToIndex = new Map();

// Build ID-to-index map for fast lookup
messagesData.forEach((msg, index) => {
    if (msg.id) {
        msgIdToIndex.set(msg.id, index);
    }
});

// Current state
let currentEditor = null;
let currentFilePath = null;
let currentBlameRanges = [];

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

    // Set width to 75% of code panel, with min/max bounds
    const codePanel = document.getElementById('code-panel');
    if (codePanel) {
        const codePanelWidth = codePanel.offsetWidth;
        const tooltipWidth = Math.min(Math.max(codePanelWidth * 0.75, 300), 800);
        blameTooltip.style.maxWidth = tooltipWidth + 'px';
    }

    blameTooltip.innerHTML = html;
    formatTimestamps(blameTooltip);
    blameTooltip.style.display = 'block';

    // Position near cursor but within viewport
    const padding = 10;
    let x = event.clientX + padding;
    let y = event.clientY + padding;

    // Measure tooltip size
    const rect = blameTooltip.getBoundingClientRect();
    const maxX = window.innerWidth - rect.width - padding;
    const maxY = window.innerHeight - rect.height - padding;

    // Handle horizontal overflow
    if (x > maxX) x = event.clientX - rect.width - padding;

    // Handle vertical overflow - prefer below cursor, shift above if needed
    if (y > maxY) {
        // Try above the cursor
        const yAbove = event.clientY - rect.height - padding;
        // Only use above position if it stays in viewport, otherwise keep below
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

// Palette of colors for blame ranges
const rangeColors = [
    'rgba(66, 165, 245, 0.15)',   // blue
    'rgba(102, 187, 106, 0.15)',  // green
    'rgba(255, 167, 38, 0.15)',   // orange
    'rgba(171, 71, 188, 0.15)',   // purple
    'rgba(239, 83, 80, 0.15)',    // red
    'rgba(38, 198, 218, 0.15)',   // cyan
];

// Extract prompt number from user_html (e.g., '<span class="index-item-number">#5</span>' -> 5)
function extractPromptNum(userHtml) {
    if (!userHtml) return null;
    const match = userHtml.match(/index-item-number">#(\d+)</);
    return match ? parseInt(match[1]) : null;
}

// Build maps for range colors and message numbers
// Ranges with the same context_msg_id get the same color
function buildRangeMaps(blameRanges) {
    const colorMap = new Map();        // range index -> color
    const msgNumMap = new Map();       // range index -> user message number
    const contextToColor = new Map();  // context_msg_id -> color
    let colorIndex = 0;

    blameRanges.forEach((range, index) => {
        if (range.msg_id) {
            // Extract prompt number for display
            const promptNum = extractPromptNum(range.user_html);
            if (promptNum) {
                msgNumMap.set(index, promptNum);
            }

            // Assign color based on context_msg_id (the assistant message providing context)
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
        // Skip pre-existing content (no color in map means it predates the session)
        const color = colorMap.get(index);
        if (!color) return;

        for (let line = range.start; line <= range.end; line++) {
            if (line <= doc.lines) {
                const lineInfo = doc.line(line);
                const lineStart = lineInfo.from;

                // Add line background decoration
                decorations.push(
                    Decoration.line({
                        attributes: {
                            style: `background-color: ${color}`,
                            'data-range-index': index.toString(),
                            'data-msg-id': range.msg_id,
                        }
                    }).range(lineStart)
                );

                // Add message number widget on first line of range
                if (line === range.start) {
                    const msgNum = msgNumMap.get(index);
                    if (msgNum) {
                        decorations.push(
                            Decoration.widget({
                                widget: new MessageNumberWidget(msgNum),
                                side: 1,  // After line content
                            }).range(lineInfo.to)
                        );
                    }
                }
            }
        }
    });

    return Decoration.set(decorations, true);
}

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

// Create the scrollbar minimap showing blame range positions
function createMinimap(container, blameRanges, totalLines, editor, colorMap) {
    // Remove existing minimap if any
    const existing = container.querySelector('.blame-minimap');
    if (existing) existing.remove();

    // Only show minimap if there are ranges with colors
    if (colorMap.size === 0 || totalLines === 0) return null;

    const minimap = document.createElement('div');
    minimap.className = 'blame-minimap';

    blameRanges.forEach((range, index) => {
        const color = colorMap.get(index);
        if (!color) return;

        const startPercent = ((range.start - 1) / totalLines) * 100;
        const endPercent = (range.end / totalLines) * 100;
        const height = Math.max(endPercent - startPercent, 0.5); // Min 0.5% height

        const marker = document.createElement('div');
        marker.className = 'minimap-marker';
        marker.style.top = startPercent + '%';
        marker.style.height = height + '%';
        marker.style.backgroundColor = color.replace('0.15', '0.6'); // More opaque
        marker.dataset.rangeIndex = index;
        marker.dataset.line = range.start;
        marker.title = `Lines ${range.start}-${range.end}`;

        // Click to scroll to that range
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

    // Create wrapper for editor + minimap
    const wrapper = document.createElement('div');
    wrapper.className = 'editor-wrapper';
    container.appendChild(wrapper);

    const editorContainer = document.createElement('div');
    editorContainer.className = 'editor-container';
    wrapper.appendChild(editorContainer);

    const doc = EditorState.create({doc: content}).doc;
    const { colorMap, msgNumMap } = buildRangeMaps(blameRanges);
    const rangeDecorations = createRangeDecorations(blameRanges, doc, colorMap, msgNumMap);

    // Static decorations as a StateField (more reliable than ViewPlugin for static decorations)
    const rangeDecorationsField = StateField.define({
        create() { return rangeDecorations; },
        update(decorations) { return decorations; },
        provide: f => EditorView.decorations.from(f)
    });

    // Click handler plugin
    const clickHandler = EditorView.domEventHandlers({
        click: (event, view) => {
            const target = event.target;
            if (target.closest('.cm-line')) {
                const line = target.closest('.cm-line');
                const rangeIndex = line.getAttribute('data-range-index');
                const msgId = line.getAttribute('data-msg-id');
                if (rangeIndex !== null) {
                    highlightRange(parseInt(rangeIndex), blameRanges, view);
                    if (msgId) {
                        scrollToMessage(msgId);
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
            // Update tooltip position when moving within highlighted line
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

    // Create minimap after editor (reuse colorMap from decorations)
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

// Render a chunk of messages to the transcript panel
function renderMessagesUpTo(targetIndex) {
    const transcriptContent = document.getElementById('transcript-content');
    const startIndex = renderedCount;

    while (renderedCount <= targetIndex && renderedCount < messagesData.length) {
        const msg = messagesData[renderedCount];
        const div = document.createElement('div');
        div.innerHTML = msg.html;
        // Append all children (the message div itself)
        while (div.firstChild) {
            transcriptContent.appendChild(div.firstChild);
        }
        renderedCount++;
    }

    // Initialize truncation and format timestamps for newly rendered messages
    if (renderedCount > startIndex) {
        initTruncation(transcriptContent);
        formatTimestamps(transcriptContent);
    }
}

// Render the next chunk of messages
function renderNextChunk() {
    const targetIndex = Math.min(renderedCount + CHUNK_SIZE - 1, messagesData.length - 1);
    renderMessagesUpTo(targetIndex);
}

// Calculate the height of sticky elements at the top of the transcript panel
function getStickyHeaderOffset() {
    const panel = document.getElementById('transcript-panel');
    const h3 = panel?.querySelector('h3');
    const pinnedMsg = document.getElementById('pinned-user-message');

    let offset = 0;
    if (h3) {
        offset += h3.offsetHeight;
    }
    if (pinnedMsg && pinnedMsg.style.display !== 'none') {
        offset += pinnedMsg.offsetHeight;
    }
    return offset + 8; // Extra padding for breathing room
}

// Scroll to a message in the transcript by msg_id
function scrollToMessage(msgId) {
    const transcriptContent = document.getElementById('transcript-content');
    const transcriptPanel = document.getElementById('transcript-panel');

    // Ensure the message is rendered first
    const msgIndex = msgIdToIndex.get(msgId);
    if (msgIndex !== undefined && msgIndex >= renderedCount) {
        renderMessagesUpTo(msgIndex);
    }

    const message = transcriptContent.querySelector(`#${msgId}`);
    if (message) {
        // Remove previous highlight
        transcriptContent.querySelectorAll('.message.highlighted').forEach(el => {
            el.classList.remove('highlighted');
        });
        // Add highlight to this message
        message.classList.add('highlighted');

        // Calculate scroll position accounting for sticky headers
        const stickyOffset = getStickyHeaderOffset();
        const messageTop = message.offsetTop;
        const targetScroll = messageTop - stickyOffset;

        transcriptPanel.scrollTo({
            top: targetScroll,
            behavior: 'smooth'
        });
    }
}

// Scroll to and highlight lines in editor
function scrollToLines(startLine, endLine) {
    if (!currentEditor) return;

    const doc = currentEditor.state.doc;
    if (startLine <= doc.lines) {
        const lineInfo = doc.line(startLine);
        currentEditor.dispatch({
            effects: EditorView.scrollIntoView(lineInfo.from, { y: 'center' })
        });
    }
}

// Load file content
function loadFile(path) {
    currentFilePath = path;

    const codeContent = document.getElementById('code-content');
    const currentFilePathEl = document.getElementById('current-file-path');

    currentFilePathEl.textContent = path;

    const data = fileData[path];
    if (!data) {
        codeContent.innerHTML = '<p style="padding: 16px;">File not found</p>';
        return;
    }

    // Create editor with content and blame ranges
    currentBlameRanges = data.blame_ranges || [];
    createEditor(codeContent, data.content || '', currentBlameRanges, path);

    // Scroll transcript to first operation for this file
    if (currentBlameRanges.length > 0 && currentBlameRanges[0].msg_id) {
        scrollToMessage(currentBlameRanges[0].msg_id);
    }
}

// File tree interaction
document.getElementById('file-tree').addEventListener('click', (e) => {
    // Handle directory toggle
    const dir = e.target.closest('.tree-dir');
    if (dir && (e.target.classList.contains('tree-toggle') || e.target.classList.contains('tree-dir-name'))) {
        dir.classList.toggle('open');
        return;
    }

    // Handle file selection
    const file = e.target.closest('.tree-file');
    if (file) {
        // Update selection state
        document.querySelectorAll('.tree-file.selected').forEach((el) => {
            el.classList.remove('selected');
        });
        file.classList.add('selected');

        // Load file content
        const path = file.dataset.path;
        loadFile(path);
    }
});

// Auto-select first file
const firstFile = document.querySelector('.tree-file');
if (firstFile) {
    firstFile.click();
}

// Resizable panels
function initResize() {
    const fileTreePanel = document.getElementById('file-tree-panel');
    const codePanel = document.getElementById('code-panel');
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
        // Hide/show resize handle when collapsed
        if (resizeLeftHandle) {
            resizeLeftHandle.style.display = fileTreePanel.classList.contains('collapsed') ? 'none' : '';
        }
        // Update button title
        collapseBtn.title = fileTreePanel.classList.contains('collapsed') ? 'Expand file tree' : 'Collapse file tree';
    });
}

// Chunked transcript rendering
// Render initial chunk of messages
renderNextChunk();

// Set up IntersectionObserver to load more messages as user scrolls
const sentinel = document.getElementById('transcript-sentinel');
if (sentinel) {
    const observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && renderedCount < messagesData.length) {
            renderNextChunk();
        }
    }, {
        root: document.getElementById('transcript-panel'),
        rootMargin: '200px',  // Start loading before sentinel is visible
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
    // Get the text content from the user message, truncated for the pinned header
    const contentEl = messageEl.querySelector('.message-content');
    if (!contentEl) return '';

    // Get text, strip extra whitespace
    let text = contentEl.textContent.trim();
    // Truncate if too long
    if (text.length > 150) {
        text = text.substring(0, 150) + '...';
    }
    return text;
}

function updatePinnedUserMessage() {
    if (!pinnedUserMessage || !transcriptContent || !transcriptPanel) return;

    // Find all user messages currently in the DOM, excluding continuations
    const allUserMessages = transcriptContent.querySelectorAll('.message.user');
    const userMessages = Array.from(allUserMessages).filter(msg => !msg.closest('.continuation'));
    if (userMessages.length === 0) {
        pinnedUserMessage.style.display = 'none';
        currentPinnedMessage = null;
        return;
    }

    // Get the scroll container's position (transcript-panel has the scroll)
    const panelRect = transcriptPanel.getBoundingClientRect();
    const headerHeight = transcriptPanel.querySelector('h3')?.offsetHeight || 0;
    const pinnedHeight = pinnedUserMessage.offsetHeight || 0;
    const topThreshold = panelRect.top + headerHeight + pinnedHeight + 10;

    // Find the user message that should be pinned:
    // The most recent user message whose top has scrolled past the threshold
    let messageToPin = null;

    for (const msg of userMessages) {
        const msgRect = msg.getBoundingClientRect();
        // If this message's top is above the threshold, it's a candidate
        if (msgRect.top < topThreshold) {
            messageToPin = msg;
        } else {
            // Messages are in order, so once we find one below threshold, stop
            break;
        }
    }

    // If the pinned message is still partially visible, check for a previous one
    if (messageToPin) {
        const msgRect = messageToPin.getBoundingClientRect();
        // If bottom of message is still visible below the header,
        // we might need the previous user message instead
        if (msgRect.bottom > topThreshold) {
            const msgArray = Array.from(userMessages);
            const idx = msgArray.indexOf(messageToPin);
            if (idx > 0) {
                // Use the previous user message
                messageToPin = msgArray[idx - 1];
            } else {
                // No previous message, don't pin anything
                messageToPin = null;
            }
        }
    }

    // Update the pinned header
    if (messageToPin && messageToPin !== currentPinnedMessage) {
        currentPinnedMessage = messageToPin;
        const text = extractUserMessageText(messageToPin);
        pinnedUserContent.textContent = text;
        pinnedUserMessage.style.display = 'block';

        // Add click handler to scroll to the original message
        pinnedUserMessage.onclick = () => {
            messageToPin.scrollIntoView({ behavior: 'smooth', block: 'start' });
        };
    } else if (!messageToPin) {
        pinnedUserMessage.style.display = 'none';
        currentPinnedMessage = null;
    }
}

// Throttle scroll handler for performance
let scrollTimeout = null;
transcriptPanel?.addEventListener('scroll', () => {
    if (scrollTimeout) return;
    scrollTimeout = setTimeout(() => {
        updatePinnedUserMessage();
        scrollTimeout = null;
    }, 16); // ~60fps
});

// Initial update after first render
setTimeout(updatePinnedUserMessage, 100);
