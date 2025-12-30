class CopyButton extends HTMLElement {
    constructor() {
        super();
        this.attachShadow({ mode: 'open' });
    }
    connectedCallback() {
        const label = this.getAttribute('label') || 'Copy';
        const style = document.createElement('style');
        style.textContent = 'button { background: transparent; border: 1px solid #757575; border-radius: 4px; padding: 2px 6px; font-size: 0.7rem; color: #757575; cursor: pointer; white-space: nowrap; font-family: inherit; } button:hover { background: rgba(0,0,0,0.05); border-color: #1976d2; color: #1976d2; } button.copied { background: #e8f5e9; border-color: #4caf50; color: #2e7d32; }';
        const btn = document.createElement('button');
        btn.textContent = label;
        btn.addEventListener('click', (e) => this.handleClick(e, btn, label));
        this.shadowRoot.appendChild(style);
        this.shadowRoot.appendChild(btn);
    }
    handleClick(e, btn, label) {
        e.preventDefault();
        e.stopPropagation();
        let content = this.getAttribute('data-content');
        if (!content) {
            const selector = this.getAttribute('data-content-from');
            if (selector) {
                const el = this.closest('.message, .index-item, .index-commit, .search-result')?.querySelector(selector) || document.querySelector(selector);
                if (el) content = el.innerText;
            }
        }
        if (content) {
            navigator.clipboard.writeText(content).then(() => {
                btn.textContent = 'Copied!';
                btn.classList.add('copied');
                setTimeout(() => { btn.textContent = label; btn.classList.remove('copied'); }, 2000);
            });
        }
    }
}
customElements.define('copy-button', CopyButton);
document.querySelectorAll('time[data-timestamp]').forEach(function(el) {
    const timestamp = el.getAttribute('data-timestamp');
    const date = new Date(timestamp);
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    const timeStr = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    if (isToday) { el.textContent = timeStr; }
    else { el.textContent = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + timeStr; }
});
document.querySelectorAll('pre.json').forEach(function(el) {
    let text = el.textContent;
    text = text.replace(/"([^"]+)":/g, '<span style="color: #ce93d8">"$1"</span>:');
    text = text.replace(/: "([^"]*)"/g, ': <span style="color: #81d4fa">"$1"</span>');
    text = text.replace(/: (\d+)/g, ': <span style="color: #ffcc80">$1</span>');
    text = text.replace(/: (true|false|null)/g, ': <span style="color: #f48fb1">$1</span>');
    el.innerHTML = text;
});
document.querySelectorAll('.truncatable').forEach(function(wrapper) {
    const content = wrapper.querySelector('.truncatable-content');
    const btn = wrapper.querySelector('.expand-btn');
    if (content.scrollHeight > 250) {
        wrapper.classList.add('truncated');
        btn.addEventListener('click', function() {
            if (wrapper.classList.contains('truncated')) { wrapper.classList.remove('truncated'); wrapper.classList.add('expanded'); btn.textContent = 'Show less'; }
            else { wrapper.classList.remove('expanded'); wrapper.classList.add('truncated'); btn.textContent = 'Show more'; }
        });
    }
});
