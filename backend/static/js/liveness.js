/**
 * Liveness Detector using MediaPipe Face Mesh
 * Measures Eye Aspect Ratio (EAR) for blinks and Nose-Cheek ratios for Head Turn.
 */
class LivenessDetector {
  constructor(videoElement, canvasElement, options = {}) {
    this.video = videoElement;
    this.canvas = canvasElement;
    this.ctx = canvasElement.getContext('2d');
    this.onProgress = options.onProgress || (() => {});
    this.onSuccess = options.onSuccess || (() => {});
    this.onError = options.onError || (() => {});

    this.faceMesh = null;
    this.cameraInstance = null;
    this.isRunning = false;

    // Challenge state
    this.blinksRequired = 2;
    this.blinksDetected = 0;
    this.isEyeClosed = false;
    this.livenessPassed = false;
    this.consecutiveFaceFrames = 0;

    this.initMediaPipe();
  }

  async initMediaPipe() {
    try {
      if (typeof FaceMesh === 'undefined') {
        console.warn('FaceMesh not loaded yet from CDN, waiting...');
        return;
      }
      this.faceMesh = new FaceMesh({
        locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/${file}`
      });

      this.faceMesh.setOptions({
        maxNumFaces: 1,
        refineLandmarks: true,
        minDetectionConfidence: 0.5,
        minTrackingConfidence: 0.5
      });

      this.faceMesh.onResults(this.onResults.bind(this));
      console.log('MediaPipe FaceMesh successfully initialized');
    } catch (e) {
      console.error('Error initializing MediaPipe:', e);
      this.onError('MediaPipe initialization failed: ' + e.message);
    }
  }

  start() {
    this.reset();
    this.isRunning = true;
    this.processFrame();
  }

  stop() {
    this.isRunning = false;
  }

  reset() {
    this.blinksDetected = 0;
    this.isEyeClosed = false;
    this.livenessPassed = false;
    this.consecutiveFaceFrames = 0;
  }

  async processFrame() {
    if (!this.isRunning) return;

    if (this.video && this.video.readyState >= 2 && this.faceMesh) {
      try {
        await this.faceMesh.send({ image: this.video });
      } catch (err) {
        // Ignored frame error during warm-up
      }
    }
    requestAnimationFrame(this.processFrame.bind(this));
  }

  // Landmark distance calculation
  dist(p1, p2) {
    return Math.hypot(p1.x - p2.x, p1.y - p2.y);
  }

  // Calculate Eye Aspect Ratio (EAR)
  calculateEAR(landmarks, eyeIndices) {
    const p1 = landmarks[eyeIndices[0]];
    const p2 = landmarks[eyeIndices[1]];
    const p3 = landmarks[eyeIndices[2]];
    const p4 = landmarks[eyeIndices[3]];
    const p5 = landmarks[eyeIndices[4]];
    const p6 = landmarks[eyeIndices[5]];

    const vertical1 = this.dist(p2, p6);
    const vertical2 = this.dist(p3, p5);
    const horizontal = this.dist(p1, p4);

    return (vertical1 + vertical2) / (2.0 * horizontal);
  }

  onResults(results) {
    if (!this.isRunning || this.livenessPassed) return;

    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    if (!results.multiFaceLandmarks || results.multiFaceLandmarks.length === 0) {
      this.consecutiveFaceFrames = 0;
      this.onProgress({
        status: 'searching',
        message: 'Center your face in the camera',
        passed: false
      });
      return;
    }

    const landmarks = results.multiFaceLandmarks[0];
    this.consecutiveFaceFrames++;

    // Left and Right Eye Landmarks for EAR
    // Left eye: 362, 385, 387, 263, 373, 380
    // Right eye: 33, 160, 158, 133, 153, 144
    const leftEAR = this.calculateEAR(landmarks, [362, 385, 387, 263, 373, 380]);
    const rightEAR = this.calculateEAR(landmarks, [33, 160, 158, 133, 153, 144]);
    const avgEAR = (leftEAR + rightEAR) / 2.0;

    // Blink threshold
    const EAR_THRESHOLD = 0.20;

    if (avgEAR < EAR_THRESHOLD) {
      if (!this.isEyeClosed) {
        this.isEyeClosed = true;
      }
    } else {
      if (this.isEyeClosed) {
        // Eye was closed and now opened -> Full blink registered!
        this.blinksDetected++;
        this.isEyeClosed = false;
        console.log(`Blink detected! Total: ${this.blinksDetected}/${this.blinksRequired}`);
      }
    }

    // Check if liveness criteria fulfilled
    if (this.blinksDetected >= this.blinksRequired) {
      this.livenessPassed = true;
      this.onProgress({
        status: 'verified',
        message: 'Liveness confirmed! Capturing photo...',
        passed: true,
        blinks: this.blinksDetected
      });
      setTimeout(() => {
        this.onSuccess();
      }, 300);
      return;
    }

    // Progress update
    this.onProgress({
      status: 'in_progress',
      message: `Please blink naturally (${this.blinksDetected}/${this.blinksRequired})`,
      passed: false,
      blinks: this.blinksDetected,
      ear: avgEAR.toFixed(2)
    });
  }
}
