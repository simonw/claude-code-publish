// CodeMirror 6 imports from CDN
import {EditorView, lineNumbers, gutter, GutterMarker, Decoration, ViewPlugin} from 'https://esm.sh/@codemirror/view@6';
import {EditorState, StateField, StateEffect} from 'https://esm.sh/@codemirror/state@6';
import {syntaxHighlighting, defaultHighlightStyle} from 'https://esm.sh/@codemirror/language@6';
import {javascript} from 'https://esm.sh/@codemirror/lang-javascript@6';
import {python} from 'https://esm.sh/@codemirror/lang-python@6';
import {html} from 'https://esm.sh/@codemirror/lang-html@6';
import {css} from 'https://esm.sh/@codemirror/lang-css@6';
import {json} from 'https://esm.sh/@codemirror/lang-json@6';
import {markdown} from 'https://esm.sh/@codemirror/lang-markdown@6';

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

// Palette of colors for blame ranges
const rangeColors = [
    'rgba(66, 165, 245, 0.15)',   // blue
    'rgba(102, 187, 106, 0.15)',  // green
    'rgba(255, 167, 38, 0.15)',   // orange
    'rgba(171, 71, 188, 0.15)',   // purple
    'rgba(239, 83, 80, 0.15)',    // red
    'rgba(38, 198, 218, 0.15)',   // cyan
];

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
function createRangeDecorations(blameRanges, doc) {
    const decorations = [];

    blameRanges.forEach((range, index) => {
        const colorIndex = index % rangeColors.length;
        const color = rangeColors[colorIndex];

        for (let line = range.start; line <= range.end; line++) {
            if (line <= doc.lines) {
                const lineStart = doc.line(line).from;
                decorations.push(
                    Decoration.line({
                        attributes: {
                            style: `background-color: ${color}`,
                            'data-range-index': index.toString(),
                            'data-msg-id': range.msg_id || '',
                        }
                    }).range(lineStart)
                );
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

// Create editor for a file
function createEditor(container, content, blameRanges, filePath) {
    container.innerHTML = '';

    const doc = EditorState.create({doc: content}).doc;
    const rangeDecorations = createRangeDecorations(blameRanges, doc);

    // Static decorations plugin
    const rangeDecorationsPlugin = ViewPlugin.define(() => ({}), {
        decorations: () => rangeDecorations
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
        }
    });

    const extensions = [
        lineNumbers(),
        EditorView.editable.of(false),
        EditorView.lineWrapping,
        syntaxHighlighting(defaultHighlightStyle),
        getLanguageExtension(filePath),
        rangeDecorationsPlugin,
        activeRangeField,
        clickHandler,
    ];

    const state = EditorState.create({
        doc: content,
        extensions: extensions,
    });

    currentEditor = new EditorView({
        state,
        parent: container,
    });

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

// Render a chunk of messages to the transcript panel
function renderMessagesUpTo(targetIndex) {
    const transcriptContent = document.getElementById('transcript-content');

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
}

// Render the next chunk of messages
function renderNextChunk() {
    const targetIndex = Math.min(renderedCount + CHUNK_SIZE - 1, messagesData.length - 1);
    renderMessagesUpTo(targetIndex);
}

// Scroll to a message in the transcript by msg_id
function scrollToMessage(msgId) {
    const transcriptContent = document.getElementById('transcript-content');

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
        // Scroll to it
        message.scrollIntoView({ behavior: 'smooth', block: 'center' });
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
