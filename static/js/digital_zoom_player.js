/**
 * Digital Video Player Zoom Utility
 * Applies smooth CSS & Canvas digital scaling to video stream elements on Zoom In (+) and Zoom Out (-).
 */

class DigitalZoomPlayer {
    constructor(videoContainerId, options = {}) {
        this.container = typeof videoContainerId === 'string' ? document.getElementById(videoContainerId) : videoContainerId;
        this.step = options.step || 0.25;
        this.maxZoom = options.maxZoom || 4.0;
        this.minZoom = options.minZoom || 1.0;
        this.currentZoom = 1.0;
        this.onZoomChange = options.onZoomChange || null;

        if (this.container) {
            this.container.style.overflow = 'hidden';
            this.container.style.position = 'relative';
        }
    }

    zoomIn() {
        if (this.currentZoom < this.maxZoom) {
            this.currentZoom = Math.min(this.maxZoom, parseFloat((this.currentZoom + this.step).toFixed(2)));
            this.applyZoom();
        }
        return this.currentZoom;
    }

    zoomOut() {
        if (this.currentZoom > this.minZoom) {
            this.currentZoom = Math.max(this.minZoom, parseFloat((this.currentZoom - this.step).toFixed(2)));
            this.applyZoom();
        }
        return this.currentZoom;
    }

    resetZoom() {
        this.currentZoom = 1.0;
        this.applyZoom();
        return this.currentZoom;
    }

    applyZoom() {
        if (!this.container) return;

        const targets = this.container.querySelectorAll('video, canvas, img');
        if (targets.length > 0) {
            targets.forEach(el => {
                el.style.transform = `scale(${this.currentZoom})`;
                el.style.transformOrigin = 'center center';
                el.style.transition = 'transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)';
            });
        } else {
            this.container.style.transform = `scale(${this.currentZoom})`;
            this.container.style.transformOrigin = 'center center';
            this.container.style.transition = 'transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)';
        }

        if (typeof this.onZoomChange === 'function') {
            this.onZoomChange(this.currentZoom);
        }
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = DigitalZoomPlayer;
}
