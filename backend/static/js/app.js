// Main Application Logic
let currentStream = null;
let livenessDetector = null;
let enrolledPhotos = [];
let enrollMode = 'new'; // 'new' | 'update'
let enrollMembersCache = [];

let currentCompanyId = localStorage.getItem('selected_company_id') || 'DEFAULT_ORG';
let adminSession = null; // { is_super_admin, auth_company_id, admin_pin, managed_company_id, company_name, admin_name, org_type }
let organizationsCache = [];
let membersCache = [];
let sessionsCache = [];
let currentLocation = { latitude: null, longitude: null, accuracy: null };

let otpDurationSeconds = 60;
let otpRemainingSeconds = 60;
let otpActiveFlag = false;

let orgModalMode = 'edit'; // 'edit' | 'create'
let faceModalTarget = null; // { company_id, student_id, name, department }
let faceModalPhotos = [];

const ORG_PROFILES = {
  general: {
    checkinTitle: 'Member Check-in',
    enrollTitle: 'Register or Update Face ID',
    adminSessionTitle: 'Session, Cutoff & Device Control',
    idLabel: 'Member ID',
    idPlaceholder: 'e.g. MEM-2026-001',
    groupLabel: 'Department / Group',
    groupPlaceholder: 'e.g. Operations',
    sessionLabel: 'Session ID',
    sessionTitleLabel: 'Session Name',
    sessionTitlePlaceholder: 'e.g. Daily Check-in'
  },
  office: {
    checkinTitle: 'Employee Check-in',
    enrollTitle: 'Register or Update Employee Face ID',
    adminSessionTitle: 'Shift, Cutoff & Device Control',
    idLabel: 'Employee ID',
    idPlaceholder: 'e.g. EMP-1042',
    groupLabel: 'Department / Team',
    groupPlaceholder: 'e.g. Software Engineering',
    sessionLabel: 'Shift / Office ID',
    sessionTitleLabel: 'Shift / Meeting Name',
    sessionTitlePlaceholder: 'e.g. Morning Shift - HQ'
  },
  school: {
    checkinTitle: 'Student Check-in',
    enrollTitle: 'Register or Update Student Face ID',
    adminSessionTitle: 'Class, Cutoff & Device Control',
    idLabel: 'Student ID / Roll No',
    idPlaceholder: 'e.g. STU-2026-042',
    groupLabel: 'Class / Department',
    groupPlaceholder: 'e.g. Computer Science - Year 3',
    sessionLabel: 'Class Session ID',
    sessionTitleLabel: 'Course / Lecture Title',
    sessionTitlePlaceholder: 'e.g. Computer Networks Lab'
  },
  event: {
    checkinTitle: 'Attendee Check-in',
    enrollTitle: 'Register or Update Attendee Face ID',
    adminSessionTitle: 'Event, Cutoff & Device Control',
    idLabel: 'Attendee / Pass ID',
    idPlaceholder: 'e.g. PASS-309',
    groupLabel: 'Organization / Role',
    groupPlaceholder: 'e.g. Speaker / Guest',
    sessionLabel: 'Event / Track ID',
    sessionTitleLabel: 'Event / Track Title',
    sessionTitlePlaceholder: 'e.g. Keynote Hall A'
  }
};

document.addEventListener('DOMContentLoaded', () => {
  initOrgMode();
  initTabs();
  loadCompanies().then(() => {
    fetchCurrentSession();
    loadCheckinSessions();
    fetchSystemStatus();
  });
  initAttendanceCamera();
  refreshGeolocation();
  startLiveClock();
  populateHostEndpoints();
  setInterval(fetchSystemStatus, 5000);
  setInterval(tickOtpCountdown, 1000);
  registerServiceWorker();
});

function escapeHtml(val) {
  return String(val ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatDateTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString();
}

function startLiveClock() {
  const pill = document.getElementById('liveClockPill');
  const tick = () => {
    if (pill) {
      pill.textContent = `Time: ${new Date().toLocaleTimeString()}`;
    }
  };
  tick();
  setInterval(tick, 1000);
}

function tickOtpCountdown() {
  const pill = document.getElementById('otpCountdownPill');
  const bar = document.getElementById('otpProgressBar');
  const badge = document.getElementById('otpTimerBadge');
  const txt = document.getElementById('otpCycleText');

  if (otpActiveFlag && otpRemainingSeconds > 0) {
    otpRemainingSeconds = Math.max(0, otpRemainingSeconds - 1);
    const pct = Math.max(0, Math.min(100, Math.round((otpRemainingSeconds / otpDurationSeconds) * 100)));
    if (pill) {
      pill.textContent = `${otpRemainingSeconds}s / ${otpDurationSeconds}s left`;
      pill.className = otpRemainingSeconds <= 10 ? 'pill warn' : 'pill ok';
    }
    if (bar) bar.style.width = `${pct}%`;
    if (badge) badge.textContent = `OTP: ${otpRemainingSeconds}s / ${otpDurationSeconds}s`;
    if (txt) txt.textContent = `OTP Duration: ${otpDurationSeconds}s • Active code expires in ${otpRemainingSeconds}s or immediately after check-in`;
  } else {
    if (pill) {
      pill.textContent = `${otpDurationSeconds}s window (ready)`;
      pill.className = 'pill info';
    }
    if (bar) bar.style.width = '100%';
    if (badge) badge.textContent = `OTP: ${otpDurationSeconds}s Cycle`;
    if (txt) txt.textContent = `OTP Duration: ${otpDurationSeconds}s • Auto-rotates immediately after successful attendance`;
  }
}

function refreshGeolocation() {
  const gpsText = document.getElementById('gpsStatusText');
  if (!navigator.geolocation) {
    if (gpsText) gpsText.textContent = 'Location: Not supported by browser';
    return;
  }
  if (gpsText) gpsText.textContent = 'Location: Acquiring GPS...';
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      currentLocation = {
        latitude: pos.coords.latitude,
        longitude: pos.coords.longitude,
        accuracy: Math.round(pos.coords.accuracy)
      };
      if (gpsText) {
        gpsText.textContent = `GPS: ${currentLocation.latitude.toFixed(5)}, ${currentLocation.longitude.toFixed(5)} (±${currentLocation.accuracy}m)`;
      }
    },
    (err) => {
      currentLocation = { latitude: null, longitude: null, accuracy: null };
      if (gpsText) {
        gpsText.textContent = `GPS: Unavailable (${err.message})`;
      }
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
  );
}

async function loadCompanies() {
  try {
    const res = await fetch('/api/companies?sort_order=desc');
    if (!res.ok) return;
    const companies = await res.json();
    const select = document.getElementById('companySelect');
    const loginSelect = document.getElementById('loginCompanySelect');

    if (select) {
      select.innerHTML = '';
      companies.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.company_id;
        opt.textContent = `${c.company_name} (${c.company_id})`;
        opt.dataset.orgType = c.org_type || 'general';
        select.appendChild(opt);
      });
      if (companies.some(c => c.company_id === currentCompanyId)) {
        select.value = currentCompanyId;
      } else if (companies.length > 0) {
        currentCompanyId = companies[0].company_id;
        select.value = currentCompanyId;
      }

      select.onchange = () => {
        currentCompanyId = select.value;
        localStorage.setItem('selected_company_id', currentCompanyId);
        const selectedOpt = select.options[select.selectedIndex];
        if (selectedOpt && selectedOpt.dataset.orgType) {
          document.getElementById('orgModeSelect').value = selectedOpt.dataset.orgType;
          applyOrgMode(selectedOpt.dataset.orgType);
        }
        fetchCurrentSession();
        loadCheckinSessions();
        fetchSystemStatus();
        loadEnrollExistingMembers();
      };
    }

    if (loginSelect) {
      const prev = loginSelect.value || 'ALL';
      loginSelect.innerHTML = '<option value="ALL">ALL — Super Admin (Manage Every Organization)</option>';
      companies.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.company_id;
        opt.textContent = `${c.company_name} (${c.company_id})`;
        loginSelect.appendChild(opt);
      });
      loginSelect.value = prev;
    }
  } catch (e) {
    console.error('Failed to load companies:', e);
  }
}

function onLoginOrgSelectChange() {
  const sel = document.getElementById('loginCompanySelect');
  const inp = document.getElementById('loginCompanyId');
  if (sel && inp) {
    inp.value = sel.value;
  }
}

function initOrgMode() {
  const select = document.getElementById('orgModeSelect');
  const saved = localStorage.getItem('org_mode') || 'general';
  if (select) {
    select.value = saved;
    applyOrgMode(saved);
    select.addEventListener('change', (e) => {
      localStorage.setItem('org_mode', e.target.value);
      applyOrgMode(e.target.value);
    });
  }
}

function applyOrgMode(mode) {
  const p = ORG_PROFILES[mode] || ORG_PROFILES.general;
  document.getElementById('checkinTitle').textContent = p.checkinTitle;
  document.getElementById('enrollTitle').textContent = p.enrollTitle;
  document.getElementById('adminSessionTitle').textContent = p.adminSessionTitle;

  document.getElementById('lblCheckinId').textContent = p.idLabel;
  document.getElementById('studentIdInput').placeholder = p.idPlaceholder;
  document.getElementById('lblCheckinSession').textContent = p.sessionLabel;

  document.getElementById('lblEnrollId').textContent = p.idLabel;
  document.getElementById('enrollStudentId').placeholder = p.idPlaceholder;
  document.getElementById('lblEnrollGroup').textContent = p.groupLabel;
  document.getElementById('enrollDept').placeholder = p.groupPlaceholder;

  document.getElementById('lblAdminSessionId').textContent = p.sessionLabel;
  document.getElementById('lblAdminSessionTitle').textContent = p.sessionTitleLabel;
  document.getElementById('teacherCourseName').placeholder = p.sessionTitlePlaceholder;
  document.getElementById('thMemberId').textContent = p.idLabel;
  document.getElementById('thGroup').textContent = p.groupLabel;
}

function populateHostEndpoints() {
  const origin = window.location.origin;
  const otpEl = document.getElementById('espOtpEndpointCode');
  const evtEl = document.getElementById('espEventEndpointCode');
  const ackEl = document.getElementById('espAckEndpointCode');
  const sucEl = document.getElementById('successEndpointCode');
  if (otpEl) otpEl.textContent = `POST ${origin}/api/device/otp`;
  if (evtEl) evtEl.textContent = `GET ${origin}/api/device/attendance-event?device_key=...`;
  if (ackEl) ackEl.textContent = `POST ${origin}/api/device/attendance-event/ack`;
  if (sucEl) sucEl.textContent = `GET ${origin}/api/success`;
}

function setPipelineStage(stage) {
  const s1 = document.getElementById('stepOtpNode');
  const s2 = document.getElementById('stepFaceNode');
  const s3 = document.getElementById('stepAckNode');
  if (!s1 || !s2 || !s3) return;
  s1.className = 'step-node active';
  s2.className = 'step-node';
  s3.className = 'step-node';
  if (stage >= 2) {
    s1.className = 'step-node done';
    s2.className = 'step-node active';
  }
  if (stage >= 3) {
    s2.className = 'step-node done';
    s3.className = 'step-node done';
  }
}

function showResult(element, type, html) {
  if (!element) return;
  element.style.display = '';
  element.className = `result-card ${type}`;
  element.innerHTML = html;
  element.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function hideResult(element) {
  if (!element) return;
  element.style.display = '';
  element.className = 'result-card';
  element.innerHTML = '';
}

function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js')
      .then(() => console.log('[PWA] Service Worker Registered'))
      .catch((err) => console.log('[PWA] SW Registration failed:', err));
  }
}

function initTabs() {
  const tabs = document.querySelectorAll('.tab-btn');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');

      tab.classList.add('active');
      const targetId = tab.getAttribute('data-target');
      const target = document.getElementById(targetId);
      if (target) target.style.display = 'block';

      if (targetId === 'tab-attendance') {
        initAttendanceCamera();
        loadCheckinSessions();
      } else if (targetId === 'tab-enroll') {
        initEnrollCamera();
        loadEnrollExistingMembers();
      } else if (targetId === 'tab-admin') {
        stopCamera();
        if (adminSession) {
          refreshAdminAll();
        }
      }
    });
  });
}

// -------------------------------------------------------------
// Camera & Video Utilities
// -------------------------------------------------------------
async function startCamera(videoElement) {
  stopCamera();
  try {
    const constraints = {
      video: {
        facingMode: 'user',
        width: { ideal: 640 },
        height: { ideal: 640 }
      },
      audio: false
    };
    currentStream = await navigator.mediaDevices.getUserMedia(constraints);
    videoElement.srcObject = currentStream;
    await videoElement.play();
    return true;
  } catch (err) {
    console.warn('Camera error:', err);
    return false;
  }
}

function stopCamera() {
  if (currentStream) {
    currentStream.getTracks().forEach(track => track.stop());
    currentStream = null;
  }
  if (livenessDetector) {
    livenessDetector.stop();
  }
}

function captureFrameBase64(videoElement) {
  if (!videoElement || videoElement.readyState < 2 || !videoElement.videoWidth) {
    return null;
  }
  const size = Math.min(videoElement.videoWidth, videoElement.videoHeight);
  const sx = (videoElement.videoWidth - size) / 2;
  const sy = (videoElement.videoHeight - size) / 2;

  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');

  ctx.translate(size, 0);
  ctx.scale(-1, 1);
  ctx.drawImage(videoElement, sx, sy, size, size, 0, 0, size, size);
  return canvas.toDataURL('image/jpeg', 0.9);
}

function readFilesAsBase64(files) {
  return Promise.all(
    Array.from(files).map(file => new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    }))
  );
}

// -------------------------------------------------------------
// Attendance Flow (60s Single-Use OTP + Immediate Rotation)
// -------------------------------------------------------------
async function loadCheckinSessions() {
  try {
    const res = await fetch(`/api/sessions?company_id=${encodeURIComponent(currentCompanyId)}&sort_order=desc`);
    if (!res.ok) return;
    const sessions = await res.json();
    const sel = document.getElementById('checkinSessionSelect');
    const inp = document.getElementById('sessionIdInput');
    if (!sel || !inp) return;

    sel.innerHTML = '';
    if (sessions.length === 0) {
      const opt = document.createElement('option');
      opt.value = inp.value || 'SESSION_DEFAULT';
      opt.textContent = inp.value || 'SESSION_DEFAULT';
      sel.appendChild(opt);
    } else {
      sessions.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.session_id;
        opt.textContent = `${s.course_name} (${s.session_id})${s.is_active ? ' • ACTIVE' : ''}`;
        sel.appendChild(opt);
      });
      const active = sessions.find(s => s.is_active === 1);
      if (active) {
        sel.value = active.session_id;
        inp.value = active.session_id;
      }
    }

    sel.onchange = () => {
      inp.value = sel.value;
    };
  } catch (e) {
    console.warn('Error loading check-in sessions:', e);
  }
}

async function initAttendanceCamera() {
  const video = document.getElementById('webcam');
  const overlay = document.getElementById('overlay');
  const guide = document.getElementById('faceGuide');
  const banner = document.getElementById('livenessText');

  setPipelineStage(1);
  const ready = await startCamera(video);
  if (!ready) {
    if (banner) banner.textContent = 'Camera unavailable - check permissions';
    return;
  }

  overlay.width = video.videoWidth || 400;
  overlay.height = video.videoHeight || 400;

  guide.className = 'face-guide';
  banner.textContent = 'Align face inside oval & blink twice';

  livenessDetector = new LivenessDetector(video, overlay, {
    onProgress: (info) => {
      banner.textContent = info.message;
      guide.className = info.passed ? 'face-guide verified' : 'face-guide';
      if (info.passed) setPipelineStage(2);
    },
    onSuccess: () => {
      setPipelineStage(2);
      const studentId = document.getElementById('studentIdInput').value.trim();
      const otp = document.getElementById('otpInput').value.trim();
      if (!studentId || !otp) {
        banner.textContent = 'Liveness OK! Enter ID & 60s OTP, then click Verify.';
        return;
      }
      triggerAttendanceSubmission();
    },
    onError: (msg) => {
      console.warn(msg);
      banner.textContent = 'Liveness helper offline - click Verify manually';
      guide.className = 'face-guide';
    }
  });

  livenessDetector.start();
}

async function triggerAttendanceSubmission() {
  const studentId = document.getElementById('studentIdInput').value.trim();
  const sessionId = document.getElementById('sessionIdInput').value.trim();
  const otp = document.getElementById('otpInput').value.trim();
  const resultCard = document.getElementById('attendanceResult');
  const submitBtn = document.getElementById('btnSubmitAttendance');

  if (!studentId || !otp) {
    showResult(
      resultCard,
      'error',
      '<strong>Missing Info:</strong> Enter your ID and the 6-digit OTP shown on the hardware display.'
    );
    return;
  }

  const video = document.getElementById('webcam');
  const imageBase64 = captureFrameBase64(video);
  if (!imageBase64) {
    showResult(resultCard, 'error', '<strong>Camera Not Ready:</strong> Please wait for the camera preview to appear.');
    return;
  }

  const now = new Date();
  const clientTime = `${now.toLocaleDateString()} ${now.toTimeString().split(' ')[0]}`;

  submitBtn.disabled = true;
  submitBtn.textContent = 'Verifying Face ID, Location & 60s OTP...';
  setPipelineStage(2);
  hideResult(resultCard);

  try {
    const res = await fetch('/api/attendance', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_id: currentCompanyId,
        member_id: studentId,
        student_id: studentId,
        session_id: sessionId,
        otp: otp,
        latitude: currentLocation.latitude,
        longitude: currentLocation.longitude,
        location_accuracy: currentLocation.accuracy,
        client_time: clientTime,
        image_base64: imageBase64
      })
    });

    const data = await res.json();
    if (res.ok) {
      setPipelineStage(3);
      const locLabel = data.location ? data.location.label : 'Not Provided';
      otpRemainingSeconds = data.next_otp_expires_in || 60;
      otpActiveFlag = true;
      tickOtpCountdown();

      showResult(
        resultCard,
        'success',
        `<h3>Face ID Verified &amp; Attendance Marked!</h3>
         <p><strong>${escapeHtml(data.name)}</strong> (${escapeHtml(data.member_id)}) &bull; ${escapeHtml(data.company_name)}</p>
         <div class="receipt-grid">
           <div><strong>Receipt:</strong> ${escapeHtml(data.attendance_id)}</div>
           <div><strong>Status:</strong> PRESENT (${escapeHtml(data.attendance_notice)})</div>
           <div><strong>OTP Status:</strong> Rotated (New ${data.next_otp_expires_in || 60}s OTP Issued)</div>
           <div><strong>Device Sync:</strong> <span id="receiptDeviceStatus">${escapeHtml(data.device_status)}</span></div>
           <div><strong>Notice Time:</strong> ${escapeHtml(data.notice_time)}</div>
           <div><strong>Location:</strong> ${escapeHtml(locLabel)}</div>
           <div><strong>Face Match:</strong> ${(data.similarity_score * 100).toFixed(1)}%</div>
         </div>
         <p style="font-size:0.78rem; margin-top:0.55rem;">Previous OTP invalidated &bull; Receipt: <a href="${escapeHtml(data.success_url)}" target="_blank" style="color:#6ee7b7;">${escapeHtml(data.success_url)}</a></p>`
      );
      document.getElementById('otpInput').value = '';
      pollReceiptAckStatus(data.attendance_id);
      fetchSystemStatus();
    } else {
      setPipelineStage(1);
      showResult(resultCard, 'error', `<strong>Check-in Failed:</strong> ${escapeHtml(data.detail || 'Face ID or OTP verification failed.')}`);
    }
  } catch (err) {
    setPipelineStage(1);
    showResult(resultCard, 'error', `<strong>Connection Error:</strong> ${escapeHtml(err.message)}`);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Verify Face ID & Mark Attendance';
    if (livenessDetector) {
      setTimeout(() => livenessDetector.reset(), 1500);
    }
  }
}

async function pollReceiptAckStatus(attendanceId) {
  let attempts = 0;
  const timer = setInterval(async () => {
    attempts++;
    if (attempts > 15) {
      clearInterval(timer);
      return;
    }
    try {
      const res = await fetch(`/api/success?attendance_id=${encodeURIComponent(attendanceId)}`);
      if (!res.ok) return;
      const data = await res.json();
      const statusEl = document.getElementById('receiptDeviceStatus');
      if (statusEl && data.receipt && data.receipt.device_status === 'ACK_BY_ESP8266') {
        statusEl.textContent = 'ACK_BY_ESP8266 (Confirmed)';
        statusEl.style.color = '#34d399';
        clearInterval(timer);
      }
    } catch (e) {}
  }, 2000);
}

// -------------------------------------------------------------
// Enrollment & Face ID Update Flow
// -------------------------------------------------------------
function setEnrollMode(mode) {
  enrollMode = mode === 'update' ? 'update' : 'new';
  document.getElementById('modeNewFaceBtn').classList.toggle('active', enrollMode === 'new');
  document.getElementById('modeUpdateFaceBtn').classList.toggle('active', enrollMode === 'update');
  const picker = document.getElementById('existingMemberPickerGroup');
  const submitBtn = document.getElementById('btnSubmitEnroll');

  if (enrollMode === 'update') {
    picker.style.display = 'flex';
    submitBtn.textContent = 'Update Face ID & Save Template';
    loadEnrollExistingMembers();
  } else {
    picker.style.display = 'none';
    submitBtn.textContent = 'Complete Registration & Save Face ID';
  }
}

async function loadEnrollExistingMembers() {
  try {
    const res = await fetch(`/api/members?company_id=${encodeURIComponent(currentCompanyId)}&sort_order=desc`);
    if (!res.ok) return;
    enrollMembersCache = await res.json();
    const sel = document.getElementById('existingMemberSelect');
    if (!sel) return;
    sel.innerHTML = '<option value="">-- Choose enrolled member --</option>';
    enrollMembersCache.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.student_id;
      opt.textContent = `${m.name} (${m.student_id}) — ${m.department || 'General'}`;
      sel.appendChild(opt);
    });
  } catch (e) {}
}

function onSelectExistingMember() {
  const id = document.getElementById('existingMemberSelect').value;
  if (!id) return;
  const member = enrollMembersCache.find(m => m.student_id === id);
  if (member) {
    document.getElementById('enrollStudentId').value = member.student_id;
    document.getElementById('enrollName').value = member.name || '';
    document.getElementById('enrollDept').value = member.department || 'General';
  }
}

async function initEnrollCamera() {
  const video = document.getElementById('enrollWebcam');
  await startCamera(video);
  updateEnrollThumbnails();
}

function captureEnrollShot() {
  const video = document.getElementById('enrollWebcam');
  const resultCard = document.getElementById('enrollResult');
  if (enrolledPhotos.length >= 3) {
    alert('Maximum 3 shots captured. Click Clear to retake.');
    return;
  }
  const b64 = captureFrameBase64(video);
  if (!b64) {
    showResult(resultCard, 'error', '<strong>Camera Not Ready:</strong> Wait for camera feed or use "Upload Photo".');
    return;
  }
  hideResult(resultCard);
  enrolledPhotos.push(b64);
  updateEnrollThumbnails();
}

async function handleEnrollFileUpload(event) {
  const files = event.target.files;
  if (!files || files.length === 0) return;
  const remaining = 3 - enrolledPhotos.length;
  if (remaining <= 0) {
    alert('Maximum 3 photos already selected. Click Clear to replace.');
    event.target.value = '';
    return;
  }
  const picked = Array.from(files).slice(0, remaining);
  const b64List = await readFilesAsBase64(picked);
  enrolledPhotos.push(...b64List);
  updateEnrollThumbnails();
  event.target.value = '';
}

function clearEnrollShots() {
  enrolledPhotos = [];
  updateEnrollThumbnails();
}

function updateEnrollThumbnails() {
  const container = document.getElementById('enrollShotsContainer');
  container.innerHTML = '';
  const labels = ['Center', 'Angle 2', 'Angle 3'];

  for (let i = 0; i < 3; i++) {
    const card = document.createElement('div');
    card.className = 'shot-card';
    if (enrolledPhotos[i]) {
      const img = document.createElement('img');
      img.src = enrolledPhotos[i];
      card.appendChild(img);
    }
    const label = document.createElement('div');
    label.className = 'shot-label';
    label.textContent = labels[i];
    card.appendChild(label);
    container.appendChild(card);
  }

  document.getElementById('btnSubmitEnroll').disabled = (enrolledPhotos.length === 0);
}

async function submitEnrollment() {
  const studentId = document.getElementById('enrollStudentId').value.trim();
  const name = document.getElementById('enrollName').value.trim();
  const dept = document.getElementById('enrollDept').value.trim();
  const resultCard = document.getElementById('enrollResult');
  const submitBtn = document.getElementById('btnSubmitEnroll');

  if (!studentId || (!name && enrollMode === 'new') || enrolledPhotos.length === 0) {
    showResult(resultCard, 'error', '<strong>Missing Info:</strong> Enter Member ID, Full Name, and capture/upload at least 1 photo.');
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = 'Saving Face ID Template...';
  hideResult(resultCard);

  try {
    const res = await fetch('/api/enroll', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_id: currentCompanyId,
        member_id: studentId,
        student_id: studentId,
        name: name,
        group: dept,
        department: dept,
        images_base64: enrolledPhotos
      })
    });

    const data = await res.json();
    if (res.ok) {
      const heading = data.updated ? 'Face ID Updated!' : 'Face ID Registered!';
      showResult(
        resultCard,
        'success',
        `<h3>${heading}</h3>
         <p>${escapeHtml(data.message)}</p>
         <p><strong>Shots Processed:</strong> ${data.shots_processed} &bull; <strong>Vector:</strong> ${data.embedding_dimensions}-D &bull; <strong>Updated:</strong> ${escapeHtml(formatDateTime(data.updated_at))}</p>`
      );
      enrolledPhotos = [];
      updateEnrollThumbnails();
      loadEnrollExistingMembers();
      if (enrollMode === 'new') {
        document.getElementById('enrollStudentId').value = '';
        document.getElementById('enrollName').value = '';
      }
    } else {
      showResult(resultCard, 'error', `<strong>Failed:</strong> ${escapeHtml(data.detail || 'No valid face detected.')}`);
    }
  } catch (err) {
    showResult(resultCard, 'error', `<strong>Error:</strong> ${escapeHtml(err.message)}`);
  } finally {
    submitBtn.disabled = (enrolledPhotos.length === 0);
    submitBtn.textContent = enrollMode === 'update'
      ? 'Update Face ID & Save Template'
      : 'Complete Registration & Save Face ID';
  }
}

// -------------------------------------------------------------
// Multi-Organization Admin Portal
// -------------------------------------------------------------
function adminHeaders() {
  if (!adminSession) return {};
  return {
    'X-Admin-Company': adminSession.auth_company_id,
    'X-Admin-Pin': adminSession.admin_pin
  };
}

async function adminLogin() {
  const compId = document.getElementById('loginCompanyId').value.trim().toUpperCase();
  const pin = document.getElementById('loginAdminPin').value.trim();
  const resCard = document.getElementById('adminLoginResult');

  if (!compId || !pin) {
    showResult(resCard, 'error', 'Enter Organization ID (or ALL) and Admin PIN.');
    return;
  }

  try {
    const res = await fetch('/api/companies/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ company_id: compId, admin_pin: pin })
    });
    const data = await res.json();
    if (!res.ok) {
      showResult(resCard, 'error', `<strong>Login Failed:</strong> ${escapeHtml(data.detail)}`);
      return;
    }

    hideResult(resCard);
    const startScope = (compId === 'ALL' || compId === 'SUPER_ADMIN')
      ? 'ALL'
      : (data.is_super_admin ? (currentCompanyId || data.company.company_id) : data.company.company_id);
    adminSession = {
      is_super_admin: Boolean(data.is_super_admin),
      auth_company_id: compId,
      managed_company_id: startScope,
      company_name: data.company.company_name,
      admin_name: data.company.admin_name,
      org_type: data.company.org_type,
      admin_pin: pin
    };

    document.getElementById('adminAuthGate').style.display = 'none';
    document.getElementById('adminWorkspace').style.display = 'block';

    const roleBadge = document.getElementById('adminRoleBadge');
    const orgManagerCard = document.getElementById('orgManagerCard');
    const newOrgBtn = document.getElementById('btnNewOrgModal');

    if (adminSession.is_super_admin) {
      roleBadge.textContent = 'Super Admin • All Organizations';
      roleBadge.className = 'pill ok';
      orgManagerCard.style.display = 'block';
      newOrgBtn.style.display = 'inline-flex';
    } else {
      roleBadge.textContent = `Org Admin • ${adminSession.managed_company_id}`;
      roleBadge.className = 'pill info';
      orgManagerCard.style.display = 'none';
      newOrgBtn.style.display = 'none';
    }

    await refreshAdminAll();
  } catch (e) {
    showResult(resCard, 'error', `<strong>Error:</strong> ${escapeHtml(e.message)}`);
  }
}

async function registerCompanyAdmin() {
  const compId = document.getElementById('regCompanyId').value.trim().toUpperCase();
  const compName = document.getElementById('regCompanyName').value.trim();
  const adminName = document.getElementById('regAdminName').value.trim();
  const adminPin = document.getElementById('regAdminPin').value.trim();
  const orgType = document.getElementById('regOrgType').value;
  const deviceKey = document.getElementById('regDeviceKey').value.trim();
  const resCard = document.getElementById('companyRegResult');

  if (!compId || !compName || !adminName || adminPin.length < 4) {
    showResult(resCard, 'error', 'Fill Organization ID, Name, Admin Name, and PIN (min 4 chars).');
    return;
  }

  try {
    const res = await fetch('/api/companies/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_id: compId,
        company_name: compName,
        admin_name: adminName,
        admin_pin: adminPin,
        org_type: orgType,
        device_key: deviceKey || null
      })
    });
    const data = await res.json();
    if (!res.ok) {
      showResult(resCard, 'error', `<strong>Registration Failed:</strong> ${escapeHtml(data.detail)}`);
      return;
    }

    await loadCompanies();
    document.getElementById('loginCompanyId').value = data.company.company_id;
    document.getElementById('loginAdminPin').value = adminPin;
    showResult(
      resCard,
      'success',
      `<strong>Organization Created!</strong> ${escapeHtml(data.company.company_name)} (${escapeHtml(data.company.company_id)}). Default session: <code>${escapeHtml(data.default_session_id)}</code>.`
    );
  } catch (e) {
    showResult(resCard, 'error', `<strong>Error:</strong> ${escapeHtml(e.message)}`);
  }
}

function adminLogout() {
  adminSession = null;
  document.getElementById('adminWorkspace').style.display = 'none';
  document.getElementById('adminAuthGate').style.display = 'grid';
  document.getElementById('loginAdminPin').value = '';
}

async function refreshAdminAll() {
  if (!adminSession) return;
  await loadAdminOverview();
  updateAdminBanner();
  await Promise.all([
    loadAdminSessions(),
    loadEnrolledMembers(),
    loadSessionAttendance()
  ]);
}

function updateAdminBanner() {
  if (!adminSession) return;
  const scope = adminSession.managed_company_id;
  const org = organizationsCache.find(o => o.company_id === scope);
  const titleEl = document.getElementById('adminBannerCompany');
  const detailEl = document.getElementById('adminBannerDetails');
  const hintEl = document.getElementById('managedOrgHint');

  if (scope === 'ALL') {
    titleEl.textContent = 'All Organizations Overview';
    detailEl.textContent = `Signed in as ${adminSession.admin_name} • Viewing all ${organizationsCache.length} organizations`;
    if (hintEl) hintEl.textContent = 'Select a specific organization above to create sessions';
  } else if (org) {
    titleEl.textContent = `${org.company_name} (${org.company_id})`;
    detailEl.textContent = `Org Admin: ${org.admin_name} • Mode: ${org.org_type} • Signed in as ${adminSession.admin_name}`;
    if (hintEl) hintEl.textContent = `Managing workspace: ${org.company_name} (${org.company_id})`;
  } else {
    titleEl.textContent = `${adminSession.company_name} (${scope})`;
    detailEl.textContent = `Administrator: ${adminSession.admin_name}`;
  }
}

async function loadAdminOverview() {
  if (!adminSession) return;
  const sortOrder = document.getElementById('orgSortSelect')?.value || 'desc';
  try {
    const res = await fetch(`/api/admin/overview?sort_order=${encodeURIComponent(sortOrder)}`, {
      headers: adminHeaders()
    });
    if (!res.ok) return;
    const data = await res.json();
    organizationsCache = data.organizations || [];

    document.getElementById('kpiOrgs').textContent = data.total_organizations ?? organizationsCache.length;
    document.getElementById('kpiMembers').textContent = data.total_members ?? 0;
    document.getElementById('kpiSessions').textContent = data.total_sessions ?? 0;
    document.getElementById('kpiAttendance').textContent = data.total_attendance ?? 0;

    // Populate scope selector inside Admin Workspace
    const scopeSel = document.getElementById('adminScopeOrgSelect');
    if (scopeSel) {
      scopeSel.innerHTML = '';
      if (adminSession.is_super_admin) {
        const allOpt = document.createElement('option');
        allOpt.value = 'ALL';
        allOpt.textContent = 'Scope: All Organizations';
        scopeSel.appendChild(allOpt);
      }
      organizationsCache.forEach(o => {
        const opt = document.createElement('option');
        opt.value = o.company_id;
        opt.textContent = `Manage: ${o.company_name} (${o.company_id})`;
        scopeSel.appendChild(opt);
      });
      if (
        adminSession.managed_company_id === 'ALL' && adminSession.is_super_admin ||
        organizationsCache.some(o => o.company_id === adminSession.managed_company_id)
      ) {
        scopeSel.value = adminSession.managed_company_id;
      } else if (organizationsCache.length > 0) {
        adminSession.managed_company_id = organizationsCache[0].company_id;
        scopeSel.value = adminSession.managed_company_id;
      }
    }

    renderOrganizationsTable();
  } catch (e) {
    console.error('Failed to load admin overview:', e);
  }
}

function renderOrganizationsTable() {
  const tbody = document.getElementById('organizationsTableBody');
  if (!tbody) return;
  const query = (document.getElementById('orgSearchInput')?.value || '').trim().toLowerCase();
  const filtered = organizationsCache.filter(o =>
    !query ||
    o.company_id.toLowerCase().includes(query) ||
    o.company_name.toLowerCase().includes(query) ||
    (o.admin_name || '').toLowerCase().includes(query)
  );

  document.getElementById('orgManagerTitle').textContent = `All Organizations Control Center (${filtered.length})`;
  tbody.innerHTML = '';

  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color: var(--text-dim);">No organizations found.</td></tr>';
    return;
  }

  filtered.forEach(o => {
    const tr = document.createElement('tr');
    const isManaged = adminSession && adminSession.managed_company_id === o.company_id;
    const activeSessLabel = o.active_session ? `${o.active_session.session_id}` : 'None';
    const devBadge = o.device_online
      ? '<span class="badge-present">Online</span>'
      : (o.device_count > 0 ? '<span class="badge-pending">Linked</span>' : '<span style="color:var(--text-dim);">None</span>');

    const canDelete = o.company_id !== 'DEFAULT_ORG';
    tr.innerHTML = `
      <td><strong>${escapeHtml(o.company_name)}</strong><div style="font-size:0.73rem; color:var(--text-dim);">${escapeHtml(o.company_id)}</div></td>
      <td><span class="badge-pending">${escapeHtml(o.org_type || 'general')}</span></td>
      <td>${escapeHtml(o.admin_name || '-')}</td>
      <td>${o.member_count ?? 0}</td>
      <td><code>${escapeHtml(activeSessLabel)}</code></td>
      <td>${devBadge}</td>
      <td>${escapeHtml(formatDateTime(o.created_at))}</td>
      <td>
        <div class="action-row">
          <button type="button" class="btn btn-xs ${isManaged ? 'btn-accent' : ''}" onclick="selectManagedOrg('${escapeHtml(o.company_id)}')">${isManaged ? 'Managing' : 'Manage'}</button>
          <button type="button" class="btn btn-outline btn-xs" onclick="openEditOrgModal('${escapeHtml(o.company_id)}')">Edit</button>
          ${canDelete ? `<button type="button" class="btn btn-danger btn-xs" onclick="deleteOrganization('${escapeHtml(o.company_id)}')">Delete</button>` : ''}
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function selectManagedOrg(companyId) {
  if (!adminSession) return;
  adminSession.managed_company_id = companyId;
  if (companyId !== 'ALL') {
    currentCompanyId = companyId;
    localStorage.setItem('selected_company_id', currentCompanyId);
    const companySel = document.getElementById('companySelect');
    if (companySel) companySel.value = currentCompanyId;
    fetchCurrentSession();
    loadCheckinSessions();
    fetchSystemStatus();
  }
  const scopeSel = document.getElementById('adminScopeOrgSelect');
  if (scopeSel) scopeSel.value = companyId;
  updateAdminBanner();
  renderOrganizationsTable();
  await Promise.all([
    loadAdminSessions(),
    loadEnrolledMembers(),
    loadSessionAttendance()
  ]);
}

function onAdminScopeOrgChange() {
  const val = document.getElementById('adminScopeOrgSelect').value;
  selectManagedOrg(val);
}

// Organization Modal (Create / Edit)
function openCreateOrgModal() {
  orgModalMode = 'create';
  document.getElementById('orgModalTitle').textContent = 'Create New Organization';
  document.getElementById('modalOrgId').value = '';
  document.getElementById('modalOrgId').disabled = false;
  document.getElementById('modalOrgName').value = '';
  document.getElementById('modalOrgAdminName').value = '';
  document.getElementById('modalOrgType').value = 'office';
  document.getElementById('modalOrgPin').value = '';
  document.getElementById('modalOrgDeviceKey').value = '';
  document.getElementById('lblModalOrgPin').textContent = 'Admin PIN (min 4 chars)';
  hideResult(document.getElementById('orgModalResult'));
  document.getElementById('orgModal').style.display = 'flex';
}

function openEditOrgModal(companyId) {
  const org = organizationsCache.find(o => o.company_id === companyId);
  if (!org) return;
  orgModalMode = 'edit';
  document.getElementById('orgModalTitle').textContent = `Edit Organization: ${org.company_id}`;
  document.getElementById('modalOrgId').value = org.company_id;
  document.getElementById('modalOrgId').disabled = true;
  document.getElementById('modalOrgName').value = org.company_name || '';
  document.getElementById('modalOrgAdminName').value = org.admin_name || '';
  document.getElementById('modalOrgType').value = org.org_type || 'general';
  document.getElementById('modalOrgPin').value = '';
  document.getElementById('modalOrgDeviceKey').value = '';
  document.getElementById('lblModalOrgPin').textContent = 'Reset Admin PIN (leave blank to keep current)';
  hideResult(document.getElementById('orgModalResult'));
  document.getElementById('orgModal').style.display = 'flex';
}

function closeOrgModal() {
  document.getElementById('orgModal').style.display = 'none';
}

async function saveOrgModal() {
  const compId = document.getElementById('modalOrgId').value.trim().toUpperCase();
  const compName = document.getElementById('modalOrgName').value.trim();
  const adminName = document.getElementById('modalOrgAdminName').value.trim();
  const orgType = document.getElementById('modalOrgType').value;
  const adminPin = document.getElementById('modalOrgPin').value.trim();
  const deviceKey = document.getElementById('modalOrgDeviceKey').value.trim();
  const resCard = document.getElementById('orgModalResult');

  if (!compId || !compName || !adminName) {
    showResult(resCard, 'error', 'Organization ID, Name, and Admin Name are required.');
    return;
  }
  if (orgModalMode === 'create' && adminPin.length < 4) {
    showResult(resCard, 'error', 'Admin PIN must be at least 4 characters.');
    return;
  }

  try {
    let res;
    if (orgModalMode === 'create') {
      res = await fetch('/api/companies/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_id: compId,
          company_name: compName,
          admin_name: adminName,
          admin_pin: adminPin,
          org_type: orgType,
          device_key: deviceKey || null
        })
      });
    } else {
      res = await fetch(`/api/companies/${encodeURIComponent(compId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...adminHeaders() },
        body: JSON.stringify({
          company_name: compName,
          admin_name: adminName,
          org_type: orgType,
          admin_pin: adminPin || null,
          device_key: deviceKey || null
        })
      });
    }

    const data = await res.json();
    if (!res.ok) {
      showResult(resCard, 'error', escapeHtml(data.detail || 'Failed to save organization.'));
      return;
    }

    await loadCompanies();
    await refreshAdminAll();
    closeOrgModal();
  } catch (e) {
    showResult(resCard, 'error', escapeHtml(e.message));
  }
}

async function deleteOrganization(companyId) {
  if (!confirm(`Delete organization '${companyId}' and all its members, sessions, and attendance records?`)) {
    return;
  }
  try {
    const res = await fetch(`/api/companies/${encodeURIComponent(companyId)}`, {
      method: 'DELETE',
      headers: adminHeaders()
    });
    const data = await res.json();
    if (!res.ok) {
      alert(data.detail || 'Failed to delete organization');
      return;
    }
    if (adminSession && adminSession.managed_company_id === companyId) {
      adminSession.managed_company_id = 'ALL';
    }
    if (currentCompanyId === companyId) {
      currentCompanyId = 'DEFAULT_ORG';
      localStorage.setItem('selected_company_id', currentCompanyId);
    }
    await loadCompanies();
    await refreshAdminAll();
  } catch (e) {
    alert('Error deleting organization: ' + e.message);
  }
}

// -------------------------------------------------------------
// Sessions Management
// -------------------------------------------------------------
async function fetchCurrentSession() {
  try {
    const res = await fetch(`/api/session/current?company_id=${encodeURIComponent(currentCompanyId)}`);
    if (res.ok) {
      const data = await res.json();
      if (data && data.session_id) {
        document.getElementById('sessionIdInput').value = data.session_id;
        const badge = document.getElementById('currentSessionBadge');
        if (badge) badge.textContent = `Session: ${data.session_id}`;
      }
    }
  } catch (e) {
    console.error('Error fetching session:', e);
  }
}

async function loadAdminSessions() {
  if (!adminSession) return;
  const scope = adminSession.managed_company_id;
  try {
    const res = await fetch(`/api/sessions?company_id=${encodeURIComponent(scope)}&sort_order=desc`);
    if (!res.ok) return;
    sessionsCache = await res.json();

    // Update Roster Session Filter dropdown
    const filterSel = document.getElementById('rosterSessionFilter');
    if (filterSel) {
      const prev = filterSel.value || 'ALL';
      filterSel.innerHTML = '<option value="ALL">All Sessions</option>';
      sessionsCache.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.session_id;
        opt.textContent = `${s.session_id} (${s.company_id})${s.is_active ? ' • ACTIVE' : ''}`;
        filterSel.appendChild(opt);
      });
      filterSel.value = Array.from(filterSel.options).some(o => o.value === prev) ? prev : 'ALL';
    }

    const tbody = document.getElementById('sessionsTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    if (sessionsCache.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color: var(--text-dim);">No sessions created yet.</td></tr>';
      return;
    }

    sessionsCache.forEach(s => {
      const tr = document.createElement('tr');
      const statusBadge = s.is_active === 1
        ? '<span class="badge-present">Active</span>'
        : '<span class="badge-pending">Inactive</span>';
      tr.innerHTML = `
        <td><strong>${escapeHtml(s.session_id)}</strong></td>
        <td>${escapeHtml(s.company_id)}</td>
        <td>${escapeHtml(s.course_name)}</td>
        <td>${escapeHtml(s.cutoff_time || 'None')}</td>
        <td>${escapeHtml(formatDateTime(s.created_at))}</td>
        <td>${statusBadge}</td>
        <td>
          <div class="action-row">
            ${s.is_active !== 1 ? `<button type="button" class="btn btn-outline btn-xs" onclick="activateOrgSession('${escapeHtml(s.company_id)}', '${escapeHtml(s.session_id)}')">Activate</button>` : ''}
            <button type="button" class="btn btn-danger btn-xs" onclick="deleteOrgSession('${escapeHtml(s.company_id)}', '${escapeHtml(s.session_id)}')">Delete</button>
          </div>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    console.error('Failed to load sessions:', e);
  }
}

async function createNewSession() {
  if (!adminSession) return;
  const targetComp = adminSession.managed_company_id === 'ALL' ? currentCompanyId : adminSession.managed_company_id;
  const sessionId = document.getElementById('teacherSessionId').value.trim();
  const courseName = document.getElementById('teacherCourseName').value.trim();
  const cutoffTime = document.getElementById('sessionCutoffTime').value.trim();

  if (!sessionId || !courseName) {
    alert('Enter Session ID and Session Name');
    return;
  }

  try {
    const res = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...adminHeaders() },
      body: JSON.stringify({
        company_id: targetComp,
        admin_company_id: adminSession.auth_company_id,
        admin_pin: adminSession.admin_pin,
        session_id: sessionId,
        session_title: courseName,
        course_name: courseName,
        cutoff_time: cutoffTime || null
      })
    });
    if (res.ok) {
      document.getElementById('sessionIdInput').value = sessionId;
      document.getElementById('currentSessionBadge').textContent = `Session: ${sessionId}`;
      document.getElementById('teacherSessionId').value = '';
      document.getElementById('teacherCourseName').value = '';
      await refreshAdminAll();
      loadCheckinSessions();
    } else {
      const err = await res.json();
      alert('Failed: ' + (err.detail || 'Error'));
    }
  } catch (e) {
    alert('Error creating session: ' + e.message);
  }
}

async function activateOrgSession(companyId, sessionId) {
  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(companyId)}/${encodeURIComponent(sessionId)}/activate`, {
      method: 'POST',
      headers: adminHeaders()
    });
    if (res.ok) {
      if (companyId === currentCompanyId) {
        fetchCurrentSession();
        loadCheckinSessions();
      }
      await refreshAdminAll();
    } else {
      const err = await res.json();
      alert(err.detail || 'Could not activate session');
    }
  } catch (e) {
    alert(e.message);
  }
}

async function deleteOrgSession(companyId, sessionId) {
  if (!confirm(`Delete session '${sessionId}' from ${companyId}?`)) return;
  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(companyId)}/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
      headers: adminHeaders()
    });
    if (res.ok) {
      await refreshAdminAll();
    }
  } catch (e) {
    alert(e.message);
  }
}

// -------------------------------------------------------------
// Enrolled Members & Face ID Update Manager
// -------------------------------------------------------------
async function loadEnrolledMembers() {
  const compId = adminSession ? adminSession.managed_company_id : currentCompanyId;
  const sortOrder = document.getElementById('memberSortSelect')?.value || 'desc';
  try {
    const res = await fetch(`/api/members?company_id=${encodeURIComponent(compId)}&sort_order=${encodeURIComponent(sortOrder)}`);
    if (!res.ok) return;
    membersCache = await res.json();
    renderEnrolledMembers();
  } catch (e) {
    console.error('Failed to load enrolled members:', e);
  }
}

function renderEnrolledMembers() {
  const tbody = document.getElementById('enrolledMembersBody');
  const countEl = document.getElementById('enrolledCount');
  if (!tbody) return;

  const query = (document.getElementById('memberSearchInput')?.value || '').trim().toLowerCase();
  const filtered = membersCache.filter(m =>
    !query ||
    m.student_id.toLowerCase().includes(query) ||
    (m.name || '').toLowerCase().includes(query) ||
    (m.company_id || '').toLowerCase().includes(query) ||
    (m.department || '').toLowerCase().includes(query)
  );

  countEl.textContent = `Enrolled Faces & Vectors (${filtered.length})`;
  tbody.innerHTML = '';

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color: var(--text-dim);">No matching faces enrolled.</td></tr>`;
    return;
  }

  filtered.forEach(m => {
    const tr = document.createElement('tr');
    const dims = m.embedding_dimensions ? `${m.embedding_dimensions}-D Vector` : 'Registered';
    const updatedStr = formatDateTime(m.updated_at || m.created_at);
    const createdStr = formatDateTime(m.created_at);

    tr.innerHTML = `
      <td><strong>${escapeHtml(m.student_id)}</strong></td>
      <td><code>${escapeHtml(m.company_id || 'DEFAULT_ORG')}</code></td>
      <td>${escapeHtml(m.name)}</td>
      <td>${escapeHtml(m.department || 'General')}</td>
      <td><span class="badge-present">${escapeHtml(dims)}</span></td>
      <td>${escapeHtml(updatedStr)}</td>
      <td>${escapeHtml(createdStr)}</td>
      <td>
        <div class="action-row">
          <button type="button" class="btn btn-outline btn-xs" onclick="openFaceModal('${escapeHtml(m.company_id || 'DEFAULT_ORG')}', '${escapeHtml(m.student_id)}')">Update Face ID</button>
          <button type="button" class="btn btn-danger btn-xs" onclick="deleteMember('${escapeHtml(m.company_id || 'DEFAULT_ORG')}', '${escapeHtml(m.student_id)}')">Delete</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function deleteMember(companyId, studentId) {
  if (!confirm(`Remove member '${studentId}' from '${companyId}'?`)) return;
  try {
    const res = await fetch(`/api/members/${encodeURIComponent(companyId)}/${encodeURIComponent(studentId)}`, {
      method: 'DELETE',
      headers: adminHeaders()
    });
    const data = await res.json();
    if (!res.ok) {
      alert(data.detail || 'Could not delete member');
      return;
    }
    await refreshAdminAll();
  } catch (e) {
    alert(e.message);
  }
}

// Update Face ID Modal
async function openFaceModal(companyId, studentId) {
  const member = membersCache.find(m => m.student_id === studentId && (m.company_id || 'DEFAULT_ORG') === companyId);
  faceModalTarget = {
    company_id: companyId,
    student_id: studentId,
    name: member?.name || '',
    department: member?.department || 'General'
  };
  faceModalPhotos = [];
  document.getElementById('faceModalTitle').textContent = `Update Face ID: ${studentId}`;
  document.getElementById('faceModalMemberInfo').value = `${companyId} / ${studentId}`;
  document.getElementById('faceModalName').value = faceModalTarget.name;
  document.getElementById('faceModalDept').value = faceModalTarget.department;
  hideResult(document.getElementById('faceModalResult'));
  renderModalFaceShots();
  document.getElementById('faceModal').style.display = 'flex';

  const video = document.getElementById('faceModalWebcam');
  await startCamera(video);
}

function closeFaceModal() {
  stopCamera();
  document.getElementById('faceModal').style.display = 'none';
  faceModalTarget = null;
  faceModalPhotos = [];
}

function captureModalFaceShot() {
  const video = document.getElementById('faceModalWebcam');
  const resCard = document.getElementById('faceModalResult');
  if (faceModalPhotos.length >= 3) {
    alert('Maximum 3 shots captured. Click Clear to replace.');
    return;
  }
  const b64 = captureFrameBase64(video);
  if (!b64) {
    showResult(resCard, 'error', 'Camera feed not active — use "Upload Photo" instead.');
    return;
  }
  hideResult(resCard);
  faceModalPhotos.push(b64);
  renderModalFaceShots();
}

async function handleModalFaceUpload(event) {
  const files = event.target.files;
  if (!files || files.length === 0) return;
  const remaining = 3 - faceModalPhotos.length;
  if (remaining <= 0) {
    alert('Maximum 3 photos selected. Click Clear to replace.');
    event.target.value = '';
    return;
  }
  const picked = Array.from(files).slice(0, remaining);
  const b64List = await readFilesAsBase64(picked);
  faceModalPhotos.push(...b64List);
  renderModalFaceShots();
  event.target.value = '';
}

function clearModalFaceShots() {
  faceModalPhotos = [];
  renderModalFaceShots();
}

function renderModalFaceShots() {
  const container = document.getElementById('faceModalShots');
  container.innerHTML = '';
  const labels = ['Shot 1', 'Shot 2', 'Shot 3'];
  for (let i = 0; i < 3; i++) {
    const card = document.createElement('div');
    card.className = 'shot-card';
    if (faceModalPhotos[i]) {
      const img = document.createElement('img');
      img.src = faceModalPhotos[i];
      card.appendChild(img);
    }
    const label = document.createElement('div');
    label.className = 'shot-label';
    label.textContent = labels[i];
    card.appendChild(label);
    container.appendChild(card);
  }
  document.getElementById('btnSaveFaceModal').disabled = (faceModalPhotos.length === 0);
}

async function submitModalFaceUpdate() {
  if (!faceModalTarget || faceModalPhotos.length === 0) return;
  const btn = document.getElementById('btnSaveFaceModal');
  const resCard = document.getElementById('faceModalResult');
  const name = document.getElementById('faceModalName').value.trim();
  const dept = document.getElementById('faceModalDept').value.trim();

  btn.disabled = true;
  btn.textContent = 'Updating Face ID...';
  hideResult(resCard);

  try {
    const res = await fetch(
      `/api/members/${encodeURIComponent(faceModalTarget.company_id)}/${encodeURIComponent(faceModalTarget.student_id)}/face`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...adminHeaders() },
        body: JSON.stringify({
          name: name,
          department: dept,
          images_base64: faceModalPhotos
        })
      }
    );
    const data = await res.json();
    if (!res.ok) {
      showResult(resCard, 'error', `<strong>Update Failed:</strong> ${escapeHtml(data.detail || 'No face detected.')}`);
      return;
    }

    showResult(
      resCard,
      'success',
      `<strong>Face ID Updated!</strong> ${escapeHtml(data.message)} (${data.shots_processed} shot(s), ${data.embedding_dimensions}-D)`
    );
    await loadEnrolledMembers();
    await loadAdminOverview();
    setTimeout(() => closeFaceModal(), 900);
  } catch (e) {
    showResult(resCard, 'error', escapeHtml(e.message));
  } finally {
    btn.disabled = (faceModalPhotos.length === 0);
    btn.textContent = 'Save Updated Face ID';
  }
}

// -------------------------------------------------------------
// Attendance Roster (Date-Sorted & Date-Filterable)
// -------------------------------------------------------------
function clearRosterFilters() {
  document.getElementById('rosterDateFilter').value = '';
  document.getElementById('rosterSessionFilter').value = 'ALL';
  loadSessionAttendance();
}

async function loadSessionAttendance() {
  const compId = adminSession ? adminSession.managed_company_id : currentCompanyId;
  const sessionFilter = document.getElementById('rosterSessionFilter')?.value || 'ALL';
  const dateFilter = document.getElementById('rosterDateFilter')?.value || '';
  const sortOrder = document.getElementById('rosterSortSelect')?.value || 'desc';

  try {
    const params = new URLSearchParams({
      company_id: compId,
      sort_order: sortOrder
    });
    if (dateFilter) params.set('date', dateFilter);

    const res = await fetch(`/api/attendance/${encodeURIComponent(sessionFilter)}?${params.toString()}`);
    if (res.ok) {
      const data = await res.json();
      renderAttendanceTable(data.records || []);
    }
  } catch (e) {
    console.error('Failed to load attendance:', e);
  }
}

function renderAttendanceTable(records) {
  const tbody = document.getElementById('attendanceTableBody');
  tbody.innerHTML = '';
  document.getElementById('attendanceCount').textContent = `Attendance Roster (${records.length})`;

  if (records.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; color: var(--text-dim);">No attendance records found for the selected date/filters.</td></tr>`;
    return;
  }

  records.forEach(r => {
    const tr = document.createElement('tr');
    const noticeType = r.attendance_notice || 'RECORDED';
    const noticeBadgeClass = noticeType === 'LATE' ? 'badge-late' : 'badge-present';
    const isAck = r.device_ack === 1 || r.device_status === 'ACK_BY_ESP8266';
    const devBadgeClass = isAck ? 'badge-present' : 'badge-pending';
    const devText = isAck ? 'ESP8266 ACK' : 'Pending Sync';
    const loc = r.location_label || 'Not Provided';
    const dateStr = formatDateTime(r.timestamp);

    tr.innerHTML = `
      <td><strong>${escapeHtml(dateStr)}</strong><div style="font-size:0.72rem; color:var(--text-dim);">${escapeHtml(r.notice_time || '')}</div></td>
      <td><strong>${escapeHtml(r.student_id)}</strong></td>
      <td><code>${escapeHtml(r.company_id || 'DEFAULT_ORG')}</code></td>
      <td>${escapeHtml(r.session_id)}</td>
      <td>${escapeHtml(r.student_name || 'N/A')}</td>
      <td>${escapeHtml(r.department || 'N/A')}</td>
      <td><span class="${noticeBadgeClass}">${escapeHtml(noticeType)}</span></td>
      <td><span class="${devBadgeClass}">${escapeHtml(devText)}</span></td>
      <td>${escapeHtml(loc)}</td>
      <td><span class="badge-present">${(Number(r.confidence || 0) * 100).toFixed(0)}%</span></td>
    `;
    tbody.appendChild(tr);
  });
}

function exportCsv() {
  const compId = adminSession ? adminSession.managed_company_id : currentCompanyId;
  const sessionFilter = document.getElementById('rosterSessionFilter')?.value || 'ALL';
  const dateFilter = document.getElementById('rosterDateFilter')?.value || '';
  const sortOrder = document.getElementById('rosterSortSelect')?.value || 'desc';

  const params = new URLSearchParams({
    company_id: compId,
    sort_order: sortOrder,
    format: 'csv'
  });
  if (dateFilter) params.set('date', dateFilter);

  window.location.href = `/api/attendance/${encodeURIComponent(sessionFilter)}?${params.toString()}`;
}

// -------------------------------------------------------------
// System Status (Database, Hardware & 60s OTP Window)
// -------------------------------------------------------------
async function fetchSystemStatus() {
  try {
    const res = await fetch(`/api/device/status?company_id=${encodeURIComponent(currentCompanyId)}`);
    if (!res.ok) return;
    const data = await res.json();

    const dbBadge = document.getElementById('dbStatusBadge');
    if (dbBadge && data.database) {
      const backend = data.database.backend;
      if (backend === 'mongodb' || backend === 'mongodb_data_api') {
        dbBadge.textContent = 'DB: MongoDB';
        dbBadge.className = 'pill ok';
      } else {
        dbBadge.textContent = 'DB: SQLite';
        dbBadge.className = 'pill warn';
      }
    }

    const espBadge = document.getElementById('espStatusBadge');
    const dev = data.esp8266 || data.esp32;
    if (espBadge && dev) {
      if (dev.online) {
        espBadge.textContent = 'Device: Online';
        espBadge.className = 'pill ok';
      } else {
        espBadge.textContent = 'Device: Waiting';
        espBadge.className = 'pill';
      }
    }

    if (data.otp_duration_seconds) {
      otpDurationSeconds = data.otp_duration_seconds;
    }
    if (data.otp_status) {
      otpActiveFlag = Boolean(data.otp_status.otp_active);
      otpRemainingSeconds = Number(data.otp_status.otp_expires_in || 0);
      tickOtpCountdown();
    }
  } catch (e) {}
}
