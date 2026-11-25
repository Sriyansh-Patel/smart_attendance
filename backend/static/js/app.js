// Main Application Logic
let currentStream = null;
let livenessDetector = null;
let enrolledPhotos = [];

const ORG_PROFILES = {
  general: {
    checkinTitle: 'Member Check-in',
    enrollTitle: 'Enroll New Member',
    adminSessionTitle: 'Active Session Control',
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
    enrollTitle: 'Enroll Employee',
    adminSessionTitle: 'Shift / Office Session Control',
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
    enrollTitle: 'Enroll New Student',
    adminSessionTitle: 'Class Session Control',
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
    enrollTitle: 'Register Attendee',
    adminSessionTitle: 'Event / Workshop Control',
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
  initAttendanceCamera();
  fetchCurrentSession();
  populateHostEndpoints();
  fetchSystemStatus();
  setInterval(fetchSystemStatus, 5000);
  registerServiceWorker();
});

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

  document.getElementById('lblAdminSessionId').textContent = `Create / Switch ${p.sessionLabel}`;
  document.getElementById('lblAdminSessionTitle').textContent = p.sessionTitleLabel;
  document.getElementById('teacherCourseName').placeholder = p.sessionTitlePlaceholder;
  document.getElementById('thMemberId').textContent = p.idLabel;
  document.getElementById('thGroup').textContent = p.groupLabel;
}

function populateHostEndpoints() {
  const origin = window.location.origin;
  const espCode = document.getElementById('espEndpointCode');
  if (espCode) espCode.textContent = `${origin}/api/device/otp`;
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

// PWA Service Worker Registration
function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js')
      .then(() => console.log('[PWA] Service Worker Registered'))
      .catch((err) => console.log('[PWA] SW Registration failed:', err));
  }
}

// Tab Switching
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
      } else if (targetId === 'tab-teacher') {
        stopCamera();
        loadSessionAttendance();
        loadEnrolledMembers();
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
    alert('Unable to access camera: ' + err.message + '\nPlease ensure you are using HTTPS or localhost and have granted camera permissions.');
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
  const canvas = document.createElement('canvas');
  canvas.width = videoElement.videoWidth;
  canvas.height = videoElement.videoHeight;
  const ctx = canvas.getContext('2d');

  ctx.translate(canvas.width, 0);
  ctx.scale(-1, 1);
  ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', 0.9);
}

// -------------------------------------------------------------
// Attendance Flow (Face + ESP32 OTP + Liveness)
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
        banner.textContent = 'Liveness OK! Enter ID & ESP32 OTP, then click Mark Attendance Now.';
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
      '<strong>Missing Info:</strong> Please enter your Member ID and the 6-digit OTP displayed on the ESP32 LED matrix.'
    );
    return;
  }

  const video = document.getElementById('webcam');
  const imageBase64 = captureFrameBase64(video);
  if (!imageBase64) {
    showResult(
      resultCard,
      'error',
      '<strong>Camera Not Ready:</strong> Please wait for the camera preview to appear and try again.'
    );
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = 'Verifying Face & OTP...';
  hideResult(resultCard);

  try {
    const res = await fetch('/api/attendance', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        member_id: studentId,
        student_id: studentId,
        session_id: sessionId,
        otp: otp,
        image_base64: imageBase64
      })
    });

    const data = await res.json();
    if (res.ok) {
      showResult(
        resultCard,
        'success',
        `<h3>Attendance Marked Successfully!</h3>
         <p><strong>Name:</strong> ${data.name} (${data.member_id || data.student_id})</p>
         <p><strong>Session:</strong> ${data.session_id}</p>
         <p><strong>ArcFace Match:</strong> ${(data.similarity_score * 100).toFixed(1)}%</p>
         <p style="font-size:0.8rem; margin-top:0.3rem;">Time: ${new Date(data.timestamp).toLocaleTimeString()}</p>`
      );
      document.getElementById('otpInput').value = '';
    } else {
      showResult(
        resultCard,
        'error',
        `<strong>Attendance Failed:</strong> ${data.detail || 'Verification unsuccessful.'}`
      );
    }
  } catch (err) {
    showResult(
      resultCard,
      'error',
      `<strong>Connection Error:</strong> ${err.message}`
    );
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Mark Attendance Now';
    if (livenessDetector) {
      setTimeout(() => livenessDetector.reset(), 1500);
    }
  }
}

// -------------------------------------------------------------
// Enrollment Flow (Multi-shot Capture)
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
    alert('Maximum 3 reference shots collected.');
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

  const submitBtn = document.getElementById('btnSubmitEnroll');
  submitBtn.disabled = (enrolledPhotos.length === 0);
}

async function submitEnrollment() {
  const studentId = document.getElementById('enrollStudentId').value.trim();
  const name = document.getElementById('enrollName').value.trim();
  const dept = document.getElementById('enrollDept').value.trim();
  const resultCard = document.getElementById('enrollResult');
  const submitBtn = document.getElementById('btnSubmitEnroll');

  if (!studentId || !name || enrolledPhotos.length === 0) {
    showResult(
      resultCard,
      'error',
      '<strong>Missing Info:</strong> Please enter an ID, Full Name, and capture at least 1 photo.'
    );
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = 'Enrolling & Extracting Face Vector...';
  hideResult(resultCard);

  try {
    const res = await fetch('/api/enroll', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
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
        `<h3>Enrollment Successful!</h3>
         <p>${data.message}</p>
         <p><strong>Shots Processed:</strong> ${data.shots_processed} &bull; <strong>Face Vector:</strong> ${data.embedding_dimensions}-D ArcFace embedding saved</p>`
      );
      enrolledPhotos = [];
      updateEnrollThumbnails();
      document.getElementById('enrollStudentId').value = '';
      document.getElementById('enrollName').value = '';
    } else {
      showResult(
        resultCard,
        'error',
        `<strong>Enrollment Failed:</strong> ${data.detail || 'No face detected. Ensure good lighting and face the camera.'}`
      );
    }
  } catch (err) {
    showResult(resultCard, 'error', `<strong>Error:</strong> ${err.message}`);
  } finally {
    submitBtn.disabled = (enrolledPhotos.length === 0);
    submitBtn.textContent = 'Complete Enrollment & Save Face Template';
  }
}

// -------------------------------------------------------------
// Admin / Teacher Dashboard & Sessions
// -------------------------------------------------------------
async function fetchCurrentSession() {
  try {
    const res = await fetch('/api/session/current');
    if (res.ok) {
      const data = await res.json();
      if (data && data.session_id) {
        document.getElementById('sessionIdInput').value = data.session_id;
        const badge = document.getElementById('currentSessionBadge');
        if (badge) badge.textContent = `Active: ${data.session_id}`;
      }
    }
  } catch (e) {
    console.error('Error fetching session:', e);
  }
}

async function createNewSession() {
  const sessionId = document.getElementById('teacherSessionId').value.trim();
  const courseName = document.getElementById('teacherCourseName').value.trim();
  if (!sessionId || !courseName) {
    alert('Enter Session ID and Session Name');
    return;
  }

  try {
    const res = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, session_title: courseName, course_name: courseName })
    });
    if (res.ok) {
      alert(`Session '${sessionId}' activated.`);
      document.getElementById('sessionIdInput').value = sessionId;
      document.getElementById('currentSessionBadge').textContent = `Active: ${sessionId}`;
      loadSessionAttendance();
    }
  } catch (e) {
    alert('Error creating session: ' + e.message);
  }
}

async function loadEnrolledMembers() {
  try {
    const res = await fetch('/api/members');
    if (!res.ok) return;
    const members = await res.json();
    const tbody = document.getElementById('enrolledMembersBody');
    const countEl = document.getElementById('enrolledCount');
    if (!tbody) return;

    countEl.textContent = `Enrolled Faces & Vectors (${members.length})`;
    tbody.innerHTML = '';

    if (members.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color: var(--text-muted);">No faces enrolled yet.</td></tr>`;
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
  const sessionId = document.getElementById('sessionIdInput').value.trim();
  if (!sessionId) return;

  try {
    const res = await fetch(`/api/attendance/${encodeURIComponent(sessionId)}`);
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
  document.getElementById('attendanceCount').textContent = `Total Present: ${records.length}`;

  if (records.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color: var(--text-muted);">No attendance records yet for this session.</td></tr>`;
    return;
  }

  records.forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>${r.student_id}</strong></td>
      <td>${r.student_name || 'N/A'}</td>
      <td>${r.department || 'N/A'}</td>
      <td>${new Date(r.timestamp).toLocaleTimeString()}</td>
      <td><span class="badge-present">${(r.confidence * 100).toFixed(0)}%</span></td>
      <td><span class="badge-present">PRESENT</span></td>
    `;
    tbody.appendChild(tr);
  });
}

function exportCsv() {
  const sessionId = document.getElementById('sessionIdInput').value.trim();
  if (!sessionId) {
    alert('Please select an active session first.');
    return;
  }
  window.location.href = `/api/attendance/${encodeURIComponent(sessionId)}?format=csv`;
}

// -------------------------------------------------------------
// System Status (Database & ESP32 Hardware Only - No OTP on Frontend)
// -------------------------------------------------------------
async function fetchSystemStatus() {
  try {
    const res = await fetch('/api/device/status');
    if (!res.ok) return;
    const data = await res.json();

    const dbBadge = document.getElementById('dbStatusBadge');
    if (dbBadge && data.database) {
      const backend = data.database.backend;
      if (backend === 'mongodb' || backend === 'mongodb_data_api') {
        dbBadge.textContent = `DB: MongoDB (${data.database.database_name})`;
        dbBadge.className = 'status-pill ok';
      } else {
        dbBadge.textContent = 'DB: SQLite Fallback';
        dbBadge.className = 'status-pill warn';
      }
    }

    const espBadge = document.getElementById('espStatusBadge');
    if (espBadge && data.esp32) {
      if (data.esp32.online) {
        espBadge.textContent = 'ESP32: Online';
        espBadge.className = 'status-pill ok';
      } else {
        espBadge.textContent = 'ESP32: Waiting for device';
        espBadge.className = 'status-pill';
      }
    }
  } catch (e) {}
}
