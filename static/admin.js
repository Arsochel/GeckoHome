// ── Lamps ──
async function toggleLamp(lamp, btn) {
    const action = btn.dataset.state === 'on' ? 'off' : 'on';
    const s = document.getElementById('click');
    s.currentTime = 0; s.play();
    btn.disabled = true;
    const r = await fetch(`/api/lamp/${lamp}/${action}`, { method: 'POST' });
    if (r.ok) setLampStatus(lamp, action === 'on');
    btn.disabled = false;
}

function setLampStatus(lamp, on) {
    const el = document.getElementById(`${lamp}-status`);
    if (el) el.innerHTML = `<span class="dot ${on ? 'on' : 'off'}"></span>${on ? 'On' : 'Off'}`;
    const btn = document.getElementById(`${lamp}-toggle`);
    if (!btn) return;
    btn.dataset.state = on ? 'on' : 'off';
    btn.className = `btn ${on ? 'btn-off' : 'btn-on'}`;
    btn.innerHTML = `<i data-lucide="power"></i>${on ? 'Выкл' : 'Вкл'}`;
    lucide.createIcons({ nodes: [btn] });
}

// ── WebSocket ──
(function connectWS() {
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/status`);
    ws.onmessage = e => {
        const d = JSON.parse(e.data);
        ['uv', 'heat'].forEach(k => { if (d[k]?.switch != null) setLampStatus(k, d[k].switch); });
    };
    ws.onclose = () => setTimeout(connectWS, 3000);
})();

// ── Stream ──
(function initStream() {
    const video = document.getElementById('stream-video');
    const overlay = document.getElementById('stream-overlay');

    async function connect() {
        overlay.textContent = 'Connecting...';
        overlay.style.display = 'flex';
        try {
            const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
            pc.ontrack = e => { video.srcObject = e.streams[0]; video.play().catch(() => {}); overlay.style.display = 'none'; };
            pc.oniceconnectionstatechange = () => {
                if (['failed', 'disconnected'].includes(pc.iceConnectionState)) { pc.close(); setTimeout(connect, 3000); }
            };
            pc.addTransceiver('video', { direction: 'recvonly' });
            pc.addTransceiver('audio', { direction: 'recvonly' });
            await pc.setLocalDescription(await pc.createOffer());
            await new Promise(res => {
                if (pc.iceGatheringState === 'complete') return res();
                const t = setTimeout(res, 3000);
                pc.onicegatheringstatechange = () => pc.iceGatheringState === 'complete' && (clearTimeout(t), res());
            });
            const r = await fetch('/api/camera/whep', { method: 'POST', headers: { 'Content-Type': 'application/sdp' }, body: pc.localDescription.sdp });
            if (!r.ok) { overlay.textContent = r.status === 503 ? 'Stream unavailable' : 'Stream error'; setTimeout(connect, 5000); return; }
            await pc.setRemoteDescription({ type: 'answer', sdp: await r.text() });
        } catch (e) {
            overlay.textContent = 'Stream error';
            setTimeout(connect, 5000);
        }
    }
    connect();
})();

function toggleFullscreen() {
    const w = document.getElementById('stream-wrap');
    document.fullscreenElement ? document.exitFullscreen() : w.requestFullscreen();
}

let _pipRaf = null;
let _pipVideo = null;

function togglePiP() {
    if (_pipVideo && document.pictureInPictureElement === _pipVideo) {
        document.exitPictureInPicture();
        return;
    }
    const src = document.getElementById('stream-video');
    const canvas = document.getElementById('pip-canvas');
    // canvas размер = повёрнутый стрим
    canvas.width  = src.videoHeight || 1080;
    canvas.height = src.videoWidth  || 1920;
    const ctx = canvas.getContext('2d');
    function draw() {
        ctx.save();
        ctx.translate(canvas.width / 2, canvas.height / 2);
        ctx.rotate(Math.PI / 2);
        ctx.drawImage(src, -canvas.height / 2, -canvas.width / 2, canvas.height, canvas.width);
        ctx.restore();
        _pipRaf = requestAnimationFrame(draw);
    }
    draw();
    if (!_pipVideo) {
        _pipVideo = document.createElement('video');
        _pipVideo.srcObject = canvas.captureStream(25);
        _pipVideo.muted = true;
        _pipVideo.addEventListener('leavepictureinpicture', () => {
            cancelAnimationFrame(_pipRaf);
        });
    } else {
        _pipVideo.srcObject = canvas.captureStream(25);
    }
    _pipVideo.play().then(() => _pipVideo.requestPictureInPicture());
}

function resizeStream() {
    const w = document.getElementById('stream-wrap');
    const v = document.getElementById('stream-video');
    const W = w.clientWidth, H = w.clientHeight;
    if (!W || !H) return;
    if (W / H <= 9 / 16) { v.style.width = (W * 16 / 9) + 'px'; v.style.height = W + 'px'; }
    else                  { v.style.width = H + 'px';             v.style.height = (H * 9 / 16) + 'px'; }
}
resizeStream();
document.addEventListener('fullscreenchange', () => setTimeout(resizeStream, 50));
window.addEventListener('resize', resizeStream);

// ── Schedules ──
async function addSchedule() {
    const [hour, minute] = document.getElementById('new-time').value.split(':').map(Number);
    const r = await fetch('/api/schedules', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lamp_type: document.getElementById('new-lamp').value, hour, minute, duration_h: +document.getElementById('new-dur').value }),
    });
    r.ok ? location.reload() : alert('Error: ' + (await r.json()).detail);
}

async function deleteSchedule(id) {
    if (!confirm('Delete this schedule?')) return;
    const r = await fetch('/api/schedules/' + id, { method: 'DELETE' });
    r.ok ? document.getElementById('row-' + id).remove() : alert('Error: ' + (await r.json()).detail);
}

async function toggleSchedule(id) {
    const r = await fetch('/api/schedules/' + id + '/toggle', { method: 'POST' });
    r.ok ? location.reload() : alert('Error: ' + (await r.json()).detail);
}

// ── Gallery ──
let _galleryOffset = 0;
let _galleryDone   = false;
let _lightboxId    = null;

async function loadGallery(reset = false) {
    if (reset) { _galleryOffset = 0; _galleryDone = false; }
    if (_galleryDone) return;
    const grid = document.getElementById('gallery');
    try {
        const r = await fetch(`/api/camera/gallery?limit=12&offset=${_galleryOffset}`);
        if (!r.ok) throw new Error(r.status);
        const photos = await r.json();
        if (reset) grid.innerHTML = '';
        if (photos.length === 0 && _galleryOffset === 0) {
            grid.innerHTML = '<div class="gallery-empty">Снимков пока нет</div>';
            document.getElementById('gallery-more-wrap').style.display = 'none';
            return;
        }
        photos.forEach(p => {
            const img = document.createElement('img');
            img.src = `/api/camera/photos/${p.id}`;
            img.className = 'gallery-thumb';
            img.dataset.id = p.id;
            img.title = p.taken_at;
            img.onclick = () => openLightbox(p.id, img.src);
            grid.appendChild(img);
        });
        _galleryOffset += photos.length;
        _galleryDone = photos.length < 12;
        document.getElementById('gallery-more-wrap').style.display = _galleryDone ? 'none' : '';
    } catch (e) {
        if (reset) grid.innerHTML = `<div class="gallery-empty">Ошибка загрузки: ${e.message}</div>`;
    }
}

function openLightbox(id, src) {
    _lightboxId = id;
    document.getElementById('lightbox-img').src = src;
    document.getElementById('lightbox').classList.add('open');
    lucide.createIcons({ nodes: [document.querySelector('.lightbox-del')] });
}

function closeLightbox() { document.getElementById('lightbox').classList.remove('open'); }

async function deleteLightboxPhoto(e) {
    e.stopPropagation();
    if (!_lightboxId) return;
    await fetch(`/api/camera/photos/${_lightboxId}`, { method: 'DELETE' });
    closeLightbox();
    loadGallery(true);
}

loadGallery(true);

// ── Camera ──
async function camCapture(type) {
    const isSnap = type === 'snapshot';
    const btnId  = isSnap ? 'snap-btn' : type === 'clip3' ? 'clip3-btn' : 'clip-btn';
    const btn    = document.getElementById(btnId);
    const status = document.getElementById('cam-status');
    const img     = document.getElementById('snap-img');
    const vid     = document.getElementById('clip-video');
    const clipWrap = document.getElementById('clip-wrap');
    btn.disabled = true;
    status.textContent = isSnap ? 'Taking snapshot...' : type === 'clip3' ? 'Recording 3 min...' : 'Recording 30s clip...';
    try {
        const url = type === 'clip3' ? '/api/camera/clip3' : '/api/camera/' + type;
        const r = await fetch(url);
        if (!r.ok) throw new Error((await r.json()).detail);
        const blobUrl = URL.createObjectURL(await r.blob());
        if (isSnap) { img.src = blobUrl; img.style.display = 'block'; clipWrap.style.display = 'none'; loadGallery(true); }
        else        { vid.src = blobUrl; clipWrap.style.display = 'block'; img.style.display = 'none'; }
        status.textContent = (isSnap ? '📸 ' : '🎬 ') + new Date().toLocaleTimeString();
    } catch (e) {
        status.textContent = 'Error: ' + e.message;
    } finally {
        btn.disabled = false;
    }
}
