export const $ = id => document.getElementById(id);
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const formatTime = seconds => { const t = Math.max(0, Number(seconds) || 0); return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`; };

/** One accessible native dialog; text supplied by callers is escaped first. */
export function askDialog(title, body, confirmLabel = '确认') {
    const dialog = $('action-dialog');
    if (dialog.open) return Promise.resolve(null);
    const focus = document.activeElement;
    $('dialog-content').innerHTML = `<h2>${escapeHTML(title)}</h2>${body}`;
    $('dialog-confirm').textContent = confirmLabel;
    $('dialog-confirm').disabled = false;
    dialog.returnValue = '';
    return new Promise(resolve => {
        dialog.addEventListener('close', () => {
            const data = dialog.returnValue === 'confirm' ? new FormData(dialog.querySelector('form')) : null;
            resolve(data);
            if (focus?.isConnected) focus.focus();
        }, {once:true});
        dialog.showModal();
    });
}

let toastTimer;
const feedbackErrors = [];
export function clearFeedback() {
    feedbackErrors.length = 0;
    $('task-errors').replaceChildren();
    $('task-errors-wrap').hidden = true;
    $('task-feedback').classList.add('hidden');
}
export function showToast(message, kind = 'info') {
    const text = String(message);
    $('app-toast').textContent = text;
    $('app-toast').className = `toast ${kind}`;
    $('app-toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('app-toast').hidden = true; }, 4000);
    if (kind === 'error' && !feedbackErrors.includes(text)) {
        feedbackErrors.push(text);
        if (feedbackErrors.length > 5) feedbackErrors.shift();
        $('task-errors').replaceChildren(...feedbackErrors.map(error => {
            const line = document.createElement('p'); line.textContent = error; return line;
        }));
    }
    $('task-errors-wrap').hidden = !feedbackErrors.length;
    $('task-feedback').className = feedbackErrors.length ? 'error' : '';
    $('task-message').textContent = text;
}
