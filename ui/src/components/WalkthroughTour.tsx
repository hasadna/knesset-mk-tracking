import React, { useState, useRef, useCallback, useLayoutEffect } from 'react';
import { XIcon, ChevronLeftIcon, ChevronRightIcon } from './icons';

export interface WalkthroughStepConfig {
  id: string;
  targetSelector: string;
  title: string;
  description: string;
  preferredPlacement?: 'start' | 'end' | 'top' | 'bottom';
  onBeforeStep?: () => Promise<void> | void;
  onAfterStep?: () => Promise<void> | void;
}

export interface WalkthroughTourProps {
  steps: WalkthroughStepConfig[];
  isActive: boolean;
  onComplete: () => void;
  onSkip: () => void;
  headerSelector?: string;
}

interface TargetRect {
  top: number;
  bottom: number;
  left: number;
  right: number;
  width: number;
  height: number;
}

export const WalkthroughTour: React.FC<WalkthroughTourProps> = ({
  steps,
  isActive,
  onComplete,
  onSkip,
  headerSelector = '.controls',
}) => {
  const [currentStepIndex, setCurrentStepIndex] = useState<number>(0);
  const [targetRect, setTargetRect] = useState<TargetRect | null>(null);
  const [announcement, setAnnouncement] = useState<string>('');
  const [isTransitioning, setIsTransitioning] = useState<boolean>(false);

  const cardRef = useRef<HTMLDivElement>(null);
  const nextBtnRef = useRef<HTMLButtonElement>(null);

  const currentStep = steps[currentStepIndex];

  const isReducedMotion = useCallback(() => {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }, []);

  const getHeaderHeight = useCallback(() => {
    if (!headerSelector) return 0;
    const header = document.querySelector(headerSelector);
    return header ? header.getBoundingClientRect().bottom : 0;
  }, [headerSelector]);

  const updateTargetPosition = useCallback(async () => {
    if (!currentStep || !isActive) return;

    setIsTransitioning(true);

    if (currentStep.onBeforeStep) {
      await currentStep.onBeforeStep();
    }

    // Poll for target DOM element availability
    let el: HTMLElement | null = null;
    const startTime = Date.now();
    while (Date.now() - startTime < 1500) {
      el = document.querySelector<HTMLElement>(currentStep.targetSelector);
      if (el && el.getBoundingClientRect().width > 0) break;
      await new Promise((res) => setTimeout(res, 50));
    }

    if (!el || el.getBoundingClientRect().width === 0) {
      console.warn(`[WalkthroughTour] Target not available, completing tour gracefully: ${currentStep.targetSelector}`);
      setIsTransitioning(false);
      handleComplete();
      return;
    }

    const headerHeight = getHeaderHeight();
    const rectInitial = el.getBoundingClientRect();
    const isObscuredByHeader = rectInitial.top < headerHeight + 16;
    const isObscuredByBottom = rectInitial.bottom > window.innerHeight - 180; // account for bottom card

    if (isObscuredByHeader || isObscuredByBottom) {
      el.scrollIntoView({
        behavior: isReducedMotion() ? 'auto' : 'smooth',
        block: 'center',
        inline: 'nearest',
      });
      if (!isReducedMotion()) {
        await new Promise((res) => setTimeout(res, 300));
      }
    }

    const rect = el.getBoundingClientRect();
    const padding = 8;
    const computedTarget: TargetRect = {
      top: Math.max(0, rect.top - padding),
      bottom: Math.min(window.innerHeight, rect.bottom + padding),
      left: Math.max(0, rect.left - padding),
      right: Math.min(window.innerWidth, rect.right + padding),
      width: rect.width + padding * 2,
      height: rect.height + padding * 2,
    };

    setTargetRect(computedTarget);
    setIsTransitioning(false);

    const srText = `שלב ${currentStepIndex + 1} מתוך ${steps.length}: ${currentStep.title}. ${currentStep.description}`;
    setAnnouncement(srText);

    requestAnimationFrame(() => {
      nextBtnRef.current?.focus();
    });
  }, [currentStep, isActive, currentStepIndex, steps.length, getHeaderHeight, isReducedMotion]);

  useLayoutEffect(() => {
    if (!isActive) return;
    updateTargetPosition();

    const handleResize = () => {
      updateTargetPosition();
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [isActive, updateTargetPosition]);

  const handleNext = async () => {
    if (currentStep?.onAfterStep) {
      await currentStep.onAfterStep();
    }
    if (currentStepIndex < steps.length - 1) {
      setCurrentStepIndex((prev) => prev + 1);
    } else {
      handleComplete();
    }
  };

  const handlePrev = async () => {
    if (currentStep?.onAfterStep) {
      await currentStep.onAfterStep();
    }
    if (currentStepIndex > 0) {
      setCurrentStepIndex((prev) => prev - 1);
    }
  };

  const handleComplete = async () => {
    if (currentStep?.onAfterStep) {
      await currentStep.onAfterStep();
    }
    setCurrentStepIndex(0);
    onComplete();
  };

  const handleSkip = async () => {
    if (currentStep?.onAfterStep) {
      await currentStep.onAfterStep();
    }
    setCurrentStepIndex(0);
    onSkip();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      handleSkip();
      return;
    }

    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      handleNext();
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      handlePrev();
    }

    if (e.key === 'Tab' && cardRef.current) {
      const focusables = cardRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusables.length === 0) return;

      const first = focusables[0];
      const last = focusables[focusables.length - 1];

      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  if (!isActive || !currentStep) return null;

  const vw = typeof window !== 'undefined' ? window.innerWidth : 1200;
  const vh = typeof window !== 'undefined' ? window.innerHeight : 800;

  return (
    <div className="walkthrough-overlay" dir="rtl" lang="he">
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>

      <svg className="walkthrough-svg-mask" width={vw} height={vh} aria-hidden="true">
        <defs>
          <mask id="walkthrough-spotlight-mask">
            <rect x="0" y="0" width={vw} height={vh} fill="white" />
            {targetRect && (
              <rect
                x={targetRect.left}
                y={targetRect.top}
                width={targetRect.width}
                height={targetRect.height}
                rx="10"
                ry="10"
                fill="black"
              />
            )}
          </mask>
        </defs>

        <rect
          x="0"
          y="0"
          width={vw}
          height={vh}
          fill="rgba(15, 23, 42, 0.72)"
          mask="url(#walkthrough-spotlight-mask)"
        />

        {targetRect && (
          <rect
            className="spotlight-ring"
            x={targetRect.left}
            y={targetRect.top}
            width={targetRect.width}
            height={targetRect.height}
            rx="10"
            ry="10"
            fill="none"
            stroke="var(--accent, #3b82f6)"
            strokeWidth="3"
          />
        )}
      </svg>

      {/* Solidly Docked Bottom Tour Card */}
      <div
        ref={cardRef}
        className="walkthrough-card docked-card"
        style={{ pointerEvents: 'auto' }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tourTitle"
        aria-describedby="tourDesc"
        onKeyDown={handleKeyDown}
        tabIndex={-1}
      >
        <header className="tour-card-header">
          <span className="tour-step-badge">
            שלב {currentStepIndex + 1} מתוך {steps.length}
          </span>
          <button
            type="button"
            className="tour-close-btn"
            onClick={handleSkip}
            aria-label="סגירת סיור מודרך"
          >
            <XIcon size={14} />
          </button>
        </header>

        <main className="tour-card-body">
          <h3 id="tourTitle" className="tour-title">
            {currentStep.title}
          </h3>
          <p id="tourDesc" className="tour-desc">
            {currentStep.description}
          </p>
        </main>

        <footer className="tour-card-footer">
          <button
            type="button"
            className="tour-btn tour-btn-skip"
            onClick={handleSkip}
          >
            דילוג
          </button>

          <div className="tour-nav-group">
            {currentStepIndex > 0 && (
              <button
                type="button"
                className="tour-btn tour-btn-secondary"
                onClick={handlePrev}
                disabled={isTransitioning}
              >
                <ChevronRightIcon size={14} /> הקודם
              </button>
            )}

            <button
              ref={nextBtnRef}
              type="button"
              className="tour-btn tour-btn-primary"
              onClick={handleNext}
              disabled={isTransitioning}
            >
              {currentStepIndex === steps.length - 1 ? 'סיום' : 'הבא'} <ChevronLeftIcon size={14} />
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
};
