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
document.querySelectorAll('time[data-timestamp]').forEach(function(el) {
    const timestamp = el.getAttribute('data-timestamp');
    const date = new Date(timestamp);
    el.textContent = formatTimestamp(date);
    el.title = date.toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'long' });
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
