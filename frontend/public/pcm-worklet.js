// Capture mono PCM at 16 kHz, independent of the device's sample rate.
class PCMCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.phase = 0;
    this.sum = 0;
    this.count = 0;
    this.chunk = new Int16Array(1600);
    this.index = 0;
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const sample of input) {
      this.sum += sample;
      this.count += 1;
      this.phase += 16000;
      if (this.phase >= sampleRate) {
        const value = Math.max(-1, Math.min(1, this.sum / this.count));
        this.chunk[this.index++] = value < 0 ? value * 32768 : value * 32767;
        this.phase -= sampleRate;
        this.sum = 0;
        this.count = 0;
        if (this.index === this.chunk.length) {
          this.port.postMessage(this.chunk.buffer, [this.chunk.buffer]);
          this.chunk = new Int16Array(1600);
          this.index = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('pcm-capture', PCMCapture);
