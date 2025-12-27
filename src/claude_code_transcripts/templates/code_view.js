// CodeMirror 6 imports from CDN
import {EditorView, lineNumbers, gutter, GutterMarker} from 'https://esm.sh/@codemirror/view@6';
import {EditorState} from 'https://esm.sh/@codemirror/state@6';
import {javascript} from 'https://esm.sh/@codemirror/lang-javascript@6';
import {python} from 'https://esm.sh/@codemirror/lang-python@6';
import {html} from 'https://esm.sh/@codemirror/lang-html@6';
import {css} from 'https://esm.sh/@codemirror/lang-css@6';
import {json} from 'https://esm.sh/@codemirror/lang-json@6';
import {markdown} from 'https://esm.sh/@codemirror/lang-markdown@6';

// File data embedded in page
const fileData = {{ file_data_json|safe }};

// Current editor instance
let currentEditor = null;

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

// Custom blame gutter marker
class BlameMarker extends GutterMarker {
    constructor(operation) {
        super();
        this.operation = operation;
    }

    toDOM() {
        const span = document.createElement('span');
        span.className = 'blame-marker';

        if (this.operation) {
            const link = document.createElement('a');
            link.href = `page-${String(this.operation.page_num).padStart(3, '0')}.html#${this.operation.msg_id}`;
            link.className = 'blame-link';
            link.title = `${this.operation.operation_type} at ${this.operation.timestamp}`;
            link.textContent = this.operation.operation_type === 'write' ? 'W' : 'E';
            span.appendChild(link);
        } else {
            span.innerHTML = '<span class="blame-initial" title="Pre-session content">-</span>';
        }

        return span;
    }
}

// Create blame gutter
function createBlameGutter(blameLines) {
    const markers = [];
    blameLines.forEach((item, idx) => {
        const op = item[1];
        markers.push(new BlameMarker(op));
    });

    return gutter({
        class: 'cm-blame-gutter',
        lineMarker: (view, line) => {
            const lineNum = view.state.doc.lineAt(line.from).number - 1;
            return markers[lineNum] || null;
        }
    });
}

// Create editor for a file
function createEditor(container, content, blameLines, filePath) {
    // Clear any existing editor
    container.innerHTML = '';

    const extensions = [
        lineNumbers(),
        EditorView.editable.of(false),
        EditorView.lineWrapping,
        getLanguageExtension(filePath),
    ];

    // Add blame gutter if we have blame data
    if (blameLines && blameLines.length > 0) {
        extensions.unshift(createBlameGutter(blameLines));
    }

    const state = EditorState.create({
        doc: content,
        extensions: extensions,
    });

    currentEditor = new EditorView({
        state,
        parent: container,
    });
}

// Render diff-only view
function renderDiffOnlyView(container, operations) {
    let html = '<div class="diff-only-view">';

    operations.forEach((op) => {
        html += '<div class="diff-operation">';
        html += '<div class="diff-header">';
        html += `<span class="diff-type">${op.operation_type.charAt(0).toUpperCase() + op.operation_type.slice(1)}</span>`;
        html += `<a href="page-${String(op.page_num).padStart(3, '0')}.html#${op.msg_id}" class="diff-link">View in transcript</a>`;
        html += `<time datetime="${op.timestamp}">${formatTimestamp(op.timestamp)}</time>`;
        html += '</div>';

        if (op.operation_type === 'write') {
            html += `<pre class="diff-content diff-write">${escapeHtml(op.content)}</pre>`;
        } else {
            html += '<div class="diff-edit">';
            html += `<div class="edit-section edit-old"><div class="edit-label">-</div><pre class="edit-content">${escapeHtml(op.old_string)}</pre></div>`;
            html += `<div class="edit-section edit-new"><div class="edit-label">+</div><pre class="edit-content">${escapeHtml(op.new_string)}</pre></div>`;
            html += '</div>';
        }
        html += '</div>';
    });

    html += '</div>';
    container.innerHTML = html;
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatTimestamp(ts) {
    try {
        const date = new Date(ts);
        return date.toLocaleTimeString();
    } catch {
        return ts;
    }
}

// Load file content
function loadFile(path) {
    const codeContent = document.getElementById('code-content');
    const currentFilePath = document.getElementById('current-file-path');

    currentFilePath.textContent = path;

    const data = fileData[path];
    if (!data) {
        codeContent.innerHTML = '<p style="padding: 16px;">File not found</p>';
        return;
    }

    if (data.diff_only) {
        renderDiffOnlyView(codeContent, data.operations);
    } else if (data.final_content !== null) {
        createEditor(codeContent, data.final_content, data.blame_lines, path);
    } else {
        // Fallback to diff-only if no final content
        renderDiffOnlyView(codeContent, data.operations);
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
