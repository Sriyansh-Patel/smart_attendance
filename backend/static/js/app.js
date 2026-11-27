// Main Application Logic
let currentStream = null;
let livenessDetector = null;
let enrolledPhotos = [];
let currentCompanyId = localStorage.getItem('selected_company_id') || 'DEFAULT_ORG';
let adminSession = null; // { company_id, company_name, admin_name, admin_pin }
let currentLocation = { latitude: null, longitude: null, accuracy: null };

const ORG_PROFILES = {
  general: {
    checkinTitle: 'Member Check-in',
    enrollTitle: 'Register Face & Profile',
    adminSessionTitle: 'Session & Notice Time Control',
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
    enrollTitle: 'Register Employee Face',
    adminSessionTitle: 'Shift & Notice Time Control',
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
    enrollTitle: 'Register Student Face',
    adminSessionTitle: 'Class & Notice Time Control',
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
    enrollTitle: 'Register Attendee Face',
    adminSessionTitle: 'Event & Notice Time Control',
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
    fetchSystemStatus();
  });
  initAttendanceCamera();
  refreshGeolocation();
  startLiveClock();
  populateHostEndpoints();
  setInterval(fetchSystemStatus, 6000);
  registerServiceWorker();
});

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
        gpsText.textContent = `Location: ${currentLocation.latitude.toFixed(5)}, ${currentLocation.longitude.toFixed(5)} (±${currentLocation.accuracy}m)`;
      }
    },
    (err) => {
      currentLocation = { latitude: null, longitude: null, accuracy: null };
      if (gpsText) {
        gpsText.textContent = `Location: Permission denied or unavailable (${err.message})`;
      }
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
  );
}

async function loadCompanies() {
  try {
    const res = await fetch('/api/companies');
    if (!res.ok) return;
    const companies = await res.json();
    const select = document.getElementById('companySelect');
    if (!select) return;
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
      fetchSystemStatus();
    };
  } catch (e) {
    console.error('Failed to load companies:', e);
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
  const espCode = document.getElementById('espEndpointCode');
  const sucCode = document.getElementById('successEndpointCode');
  if (espCode) espCode.textContent = `${origin}/api/device/otp`;
  if (sucCode) sucCode.textContent = `${origin}/api/success`;
}

function showResult(element, type, html) {
  element.style.display = '';
  element.className = `result-card ${type}`;
  element.innerHTML = html;
  element.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function hideResult(element) {
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
      } else if (targetId === 'tab-enroll') {
        initEnrollCamera();
      } else if (targetId === 'tab-admin') {
        stopCamera();
        if (adminSession) {
          loadSessionAttendance();
          loadEnrolledMembers();
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
    console.error('Camera error:', err);
    alert('Unable to access camera: ' + err.message + '\nEnsure you are using HTTPS or localhost and granted camera permissions.');
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

  // Center-crop square and mirror horizontally to match preview
  ctx.translate(size, 0);
  ctx.scale(-1, 1);
  ctx.drawImage(videoElement, sx, sy, size, size, 0, 0, size, size);
  return canvas.toDataURL('image/jpeg', 0.9);
}

// -------------------------------------------------------------
// Attendance Flow (Single-User Lock + Location + Notice Time)
// -------------------------------------------------------------
async function initAttendanceCamera() {
  const video = document.getElementById('webcam');
  const overlay = document.getElementById('overlay');
  const guide = document.getElementById('faceGuide');
  const banner = document.getElementById('livenessText');

  const ready = await startCamera(video);
  if (!ready) return;

  overlay.width = video.videoWidth || 400;
  overlay.height = video.videoHeight || 400;

  guide.className = 'face-guide';
  banner.textContent = 'Align face inside oval & blink twice';

  livenessDetector = new LivenessDetector(video, overlay, {
    onProgress: (info) => {
      banner.textContent = info.message;
      guide.className = info.passed ? 'face-guide verified' : 'face-guide';
    },
    onSuccess: () => {
      const studentId = document.getElementById('studentIdInput').value.trim();
      const otp = document.getElementById('otpInput').value.trim();
      if (!studentId || !otp) {
        banner.textContent = 'Liveness verified! Enter ID & ESP32 OTP, then click Mark Attendance Now.';
        return;
      }
      triggerAttendanceSubmission();
    },
    onError: (msg) => {
      console.warn(msg);
      banner.textContent = 'Liveness helper offline - click Mark Attendance Now manually';
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
      '<strong>Missing Info:</strong> Enter your ID and the 6-digit OTP shown on the physical ESP32 LED display.'
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
  submitBtn.textContent = 'Verifying Face, Location & OTP...';
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
      const locLabel = data.location ? data.location.label : 'Not Provided';
      showResult(
        resultCard,
        'success',
        `<h3>Attendance Confirmed</h3>
         <p><strong>${data.name}</strong> (${data.member_id}) &bull; ${data.company_name}</p>
         <div class="receipt-grid">
           <div><strong>Receipt:</strong> ${data.attendance_id}</div>
           <div><strong>Notice Time:</strong> ${data.notice_time}</div>
           <div><strong>Location:</strong> ${locLabel}</div>
           <div><strong>Face Match:</strong> ${(data.similarity_score * 100).toFixed(1)}%</div>
         </div>
         <p style="font-size:0.78rem; margin-top:0.5rem;">Verified via <a href="${data.success_url}" target="_blank" style="color:#6ee7b7;">${data.success_url}</a></p>`
      );
      document.getElementById('otpInput').value = '';
    } else {
      showResult(resultCard, 'error', `<strong>Check-in Failed:</strong> ${data.detail || 'Verification unsuccessful.'}`);
    }
  } catch (err) {
    showResult(resultCard, 'error', `<strong>Connection Error:</strong> ${err.message}`);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Mark Attendance Now';
    if (livenessDetector) {
      setTimeout(() => livenessDetector.reset(), 1500);
    }
  }
}

// -------------------------------------------------------------
// Enrollment Flow (Centered Face Capture)
// -------------------------------------------------------------
async function initEnrollCamera() {
  const video = document.getElementById('enrollWebcam');
  await startCamera(video);
  enrolledPhotos = [];
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
    showResult(resultCard, 'error', '<strong>Camera Not Ready:</strong> Wait for camera feed before capturing.');
    return;
  }
  hideResult(resultCard);
  enrolledPhotos.push(b64);
  updateEnrollThumbnails();
}

function clearEnrollShots() {
  enrolledPhotos = [];
  updateEnrollThumbnails();
}

function updateEnrollThumbnails() {
  const container = document.getElementById('enrollShotsContainer');
  container.innerHTML = '';
  const labels = ['Center', 'Slight Left', 'Slight Right'];

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

  if (!studentId || !name || enrolledPhotos.length === 0) {
    showResult(resultCard, 'error', '<strong>Missing Info:</strong> Enter an ID, Full Name, and capture at least 1 photo.');
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = 'Saving Face Template...';
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
      showResult(
        resultCard,
        'success',
        `<h3>Face Registered!</h3>
         <p>${data.message}</p>
         <p><strong>Shots Processed:</strong> ${data.shots_processed} &bull; <strong>Vector:</strong> ${data.embedding_dimensions}-D saved</p>`
      );
      enrolledPhotos = [];
      updateEnrollThumbnails();
      document.getElementById('enrollStudentId').value = '';
      document.getElementById('enrollName').value = '';
    } else {
      showResult(resultCard, 'error', `<strong>Registration Failed:</strong> ${data.detail || 'No valid face detected.'}`);
    }
  } catch (err) {
    showResult(resultCard, 'error', `<strong>Error:</strong> ${err.message}`);
  } finally {
    submitBtn.disabled = (enrolledPhotos.length === 0);
    submitBtn.textContent = 'Complete Registration & Save Face';
  }
}

// -------------------------------------------------------------
// Separate Administrator / Company Portal
// -------------------------------------------------------------
async function adminLogin() {
  const compId = document.getElementById('loginCompanyId').value.trim().toUpperCase();
  const pin = document.getElementById('loginAdminPin').value.trim();
  const resCard = document.getElementById('adminLoginResult');

  if (!compId || !pin) {
    showResult(resCard, 'error', 'Enter both Company ID and Admin PIN.');
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
      showResult(resCard, 'error', `<strong>Login Failed:</strong> ${data.detail}`);
      return;
    }

    hideResult(resCard);
    adminSession = {
      company_id: data.company.company_id,
      company_name: data.company.company_name,
      admin_name: data.company.admin_name,
      org_type: data.company.org_type,
      admin_pin: pin
    };
    currentCompanyId = adminSession.company_id;
    localStorage.setItem('selected_company_id', currentCompanyId);
    const companySel = document.getElementById('companySelect');
    if (companySel) companySel.value = currentCompanyId;

    document.getElementById('adminAuthGate').style.display = 'none';
    document.getElementById('adminWorkspace').style.display = 'block';
    document.getElementById('adminBannerCompany').textContent = `${adminSession.company_name} (${adminSession.company_id})`;
    document.getElementById('adminBannerDetails').textContent = `Administrator: ${adminSession.admin_name} • Mode: ${adminSession.org_type}`;

    if (data.active_session && data.active_session.session_id) {
      document.getElementById('sessionIdInput').value = data.active_session.session_id;
      document.getElementById('teacherSessionId').value = data.active_session.session_id;
    }
    loadEnrolledMembers();
    loadSessionAttendance();
  } catch (e) {
    showResult(resCard, 'error', `<strong>Error:</strong> ${e.message}`);
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
    showResult(resCard, 'error', 'Fill Company ID, Company Name, Admin Name, and PIN (min 4 chars).');
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
      showResult(resCard, 'error', `<strong>Registration Failed:</strong> ${data.detail}`);
      return;
    }

    await loadCompanies();
    document.getElementById('loginCompanyId').value = data.company.company_id;
    document.getElementById('loginAdminPin').value = adminPin;
    showResult(
      resCard,
      'success',
      `<strong>Company Created!</strong> ${data.company.company_name} (${data.company.company_id}). Default session: <code>${data.default_session_id}</code>. Sign in on the left to manage.`
    );
  } catch (e) {
    showResult(resCard, 'error', `<strong>Error:</strong> ${e.message}`);
  }
}

function adminLogout() {
  adminSession = null;
  document.getElementById('adminWorkspace').style.display = 'none';
  document.getElementById('adminAuthGate').style.display = 'grid';
  document.getElementById('loginAdminPin').value = '';
}

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

async function createNewSession() {
  if (!adminSession) return;
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
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_id: adminSession.company_id,
        admin_pin: adminSession.admin_pin,
        session_id: sessionId,
        session_title: courseName,
        course_name: courseName,
        cutoff_time: cutoffTime || null
      })
    });
    if (res.ok) {
      alert(`Session '${sessionId}' activated for ${adminSession.company_name}.`);
      document.getElementById('sessionIdInput').value = sessionId;
      document.getElementById('currentSessionBadge').textContent = `Session: ${sessionId}`;
      loadSessionAttendance();
    } else {
      const err = await res.json();
      alert('Failed: ' + (err.detail || 'Error'));
    }
  } catch (e) {
    alert('Error creating session: ' + e.message);
  }
}

async function loadEnrolledMembers() {
  const compId = adminSession ? adminSession.company_id : currentCompanyId;
  try {
    const res = await fetch(`/api/members?company_id=${encodeURIComponent(compId)}`);
    if (!res.ok) return;
    const members = await res.json();
    const tbody = document.getElementById('enrolledMembersBody');
    const countEl = document.getElementById('enrolledCount');
    if (!tbody) return;

    countEl.textContent = `Enrolled Faces & Vectors (${members.length})`;
    tbody.innerHTML = '';

    if (members.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color: var(--text-secondary);">No faces enrolled under ${compId} yet.</td></tr>`;
      return;
    }

    members.forEach(m => {
      const tr = document.createElement('tr');
      const dims = m.embedding_dimensions ? `${m.embedding_dimensions}-D Vector` : 'Registered';
      tr.innerHTML = `
        <td><strong>${m.student_id}</strong></td>
        <td>${m.name}</td>
        <td>${m.department || 'General'}</td>
        <td><span class="badge-present">${dims}</span></td>
        <td>${m.created_at ? new Date(m.created_at).toLocaleString() : '-'}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    console.error('Failed to load enrolled members:', e);
  }
}

async function loadSessionAttendance() {
  const compId = adminSession ? adminSession.company_id : currentCompanyId;
  const sessionId = document.getElementById('sessionIdInput').value.trim();
  if (!sessionId) return;

  try {
    const res = await fetch(`/api/attendance/${encodeURIComponent(sessionId)}?company_id=${encodeURIComponent(compId)}`);
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
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color: var(--text-secondary);">No attendance records yet for this session.</td></tr>`;
    return;
  }

  records.forEach(r => {
    const tr = document.createElement('tr');
    const noticeType = r.attendance_notice || 'RECORDED';
    const noticeBadgeClass = noticeType === 'LATE' ? 'badge-late' : 'badge-present';
    const loc = r.location_label || 'Not Provided';
    const timeStr = r.notice_time || new Date(r.timestamp).toLocaleString();
    tr.innerHTML = `
      <td><strong>${r.student_id}</strong></td>
      <td>${r.student_name || 'N/A'}</td>
      <td>${r.department || 'N/A'}</td>
      <td>${timeStr}</td>
      <td><span class="${noticeBadgeClass}">${noticeType}</span></td>
      <td>${loc}</td>
      <td><span class="badge-present">${(r.confidence * 100).toFixed(0)}%</span></td>
    `;
    tbody.appendChild(tr);
  });
}

function exportCsv() {
  const compId = adminSession ? adminSession.company_id : currentCompanyId;
  const sessionId = document.getElementById('sessionIdInput').value.trim();
  if (!sessionId) {
    alert('Please select an active session first.');
    return;
  }
  window.location.href = `/api/attendance/${encodeURIComponent(sessionId)}?company_id=${encodeURIComponent(compId)}&format=csv`;
}

// -------------------------------------------------------------
// System Status (Database & ESP32 Hardware Only)
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
        dbBadge.textContent = `DB: MongoDB`;
        dbBadge.className = 'pill ok';
      } else {
        dbBadge.textContent = 'DB: SQLite';
        dbBadge.className = 'pill warn';
      }
    }

    const espBadge = document.getElementById('espStatusBadge');
    if (espBadge && data.esp32) {
      if (data.esp32.online) {
        espBadge.textContent = 'ESP32: Online';
        espBadge.className = 'pill ok';
      } else {
        espBadge.textContent = 'ESP32: Waiting';
        espBadge.className = 'pill';
      }
    }
  } catch (e) {}
}
