import React, { useEffect, useRef, useCallback } from 'react';
import { Topic } from '../types';
import { SparklesIcon, CompassIcon, XIcon, ArrowRightIcon } from './icons';

export interface WelcomeModalProps {
  isOpen: boolean;
  onClose: () => void;
  onStartTour: () => void;
  onSelectTopic: (topicId: string) => void;
  topics: Topic[];
}

export const STORAGE_KEY_WELCOME_DISMISSED = 'mk_tracking_welcome_dismissed_v1';

export const WelcomeModal: React.FC<WelcomeModalProps> = ({
  isOpen,
  onClose,
  onStartTour,
  onSelectTopic,
  topics,
}) => {
  const modalRef = useRef<HTMLDivElement>(null);
  const primaryBtnRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (isOpen) {
      previousFocusRef.current = document.activeElement as HTMLElement;
      requestAnimationFrame(() => {
        primaryBtnRef.current?.focus();
      });
    } else if (previousFocusRef.current) {
      previousFocusRef.current.focus();
    }
  }, [isOpen]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
        return;
      }

      if (e.key === 'Tab' && modalRef.current) {
        const focusables = modalRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusables.length === 0) return;

        const firstElement = focusables[0];
        const lastElement = focusables[focusables.length - 1];

        if (e.shiftKey) {
          if (document.activeElement === firstElement) {
            e.preventDefault();
            lastElement.focus();
          }
        } else {
          if (document.activeElement === lastElement) {
            e.preventDefault();
            firstElement.focus();
          }
        }
      }
    },
    [onClose]
  );

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  const handleTopicClick = (topicId: string) => {
    onSelectTopic(topicId);
    onClose();
  };

  if (!isOpen) return null;

  // Key topics to display in quick launcher
  const keyTopicIds = ['defense', 'economy', 'judiciary', 'religion_state'];
  const featuredTopics = topics.filter((t) => keyTopicIds.includes(t.id));
  const displayTopics = featuredTopics.length > 0 ? featuredTopics : topics.slice(0, 4);

  return (
    <div
      className="welcome-modal-overlay"
      onClick={handleBackdropClick}
      role="presentation"
      dir="rtl"
      lang="he"
    >
      <div
        ref={modalRef}
        className="welcome-modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="welcomeTitle"
        aria-describedby="welcomeDesc"
        onKeyDown={handleKeyDown}
        tabIndex={-1}
      >
        <button
          type="button"
          className="welcome-close-btn"
          onClick={onClose}
          aria-label="סגירת חלון ברוכים הבאים"
        >
          <XIcon size={18} />
        </button>

        <header className="welcome-modal-header">
          <div className="welcome-badge">
            <SparklesIcon size={16} /> מעקב עמדות חברי הכנסת
          </div>
          <h2 id="welcomeTitle" className="welcome-title">
            מעקב עמדות חברי הכנסת
          </h2>
          <p id="welcomeDesc" className="welcome-subtitle">
            מערכת שקופה להצלבת הצהרות חברי הכנסת ברשתות החברתיות עם מגוון נושאי מדיניות.
          </p>
        </header>

        <div className="welcome-modal-body">
          <div className="welcome-feature-grid">
            <div className="welcome-feature-item">
              <div className="feature-icon-wrap">🎯</div>
              <div>
                <strong>ניתוח עמדות:</strong> זיהוי עמדות חברי הכנסת בנושאי ליבה כמו ביטחון, כלכלה ומשפט.
              </div>
            </div>
            <div className="welcome-feature-item">
              <div className="feature-icon-wrap">⚖️</div>
              <div>
                <strong>שקיפות מלאה:</strong> הצגת מקור ההתבטאות וקישור ישיר לציוץ המקורי ב-X.
              </div>
            </div>
            <div className="welcome-feature-item">
              <div className="feature-icon-wrap">📊</div>
              <div>
                <strong>חלוקה לפי סיעות:</strong> השוואה בין חברי כנסת, מפלגות וגושים פוליטיים.
              </div>
            </div>
          </div>

          <div className="welcome-topic-launcher">
            <div className="topic-launcher-title">
              <CompassIcon size={15} /> עיון מהיר לפי נושא:
            </div>
            <div className="welcome-topic-grid">
              {displayTopics.map((topic) => (
                <button
                  key={topic.id}
                  type="button"
                  className="welcome-topic-btn"
                  onClick={() => handleTopicClick(topic.id)}
                >
                  <span className="welcome-topic-name">{topic.title}</span>
                  <span className="welcome-topic-arrow">←</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        <footer className="welcome-modal-footer">
          <button
            ref={primaryBtnRef}
            type="button"
            className="welcome-btn welcome-btn-primary"
            onClick={() => {
              onClose();
              onStartTour();
            }}
          >
            <span>התחל סיור קצר במערכת</span>
            <ArrowRightIcon size={18} style={{ transform: 'rotate(180deg)' }} />
          </button>

          <button
            type="button"
            className="welcome-btn welcome-btn-secondary"
            onClick={onClose}
          >
            מעבר ללוח חברי הכנסת
          </button>
        </footer>
      </div>
    </div>
  );
};

export const useWelcomeState = () => {
  const [shouldShow, setShouldShow] = React.useState<boolean>(false);

  useEffect(() => {
    const hasDismissed = localStorage.getItem(STORAGE_KEY_WELCOME_DISMISSED);
    if (!hasDismissed) {
      setShouldShow(true);
    }
  }, []);

  const dismissWelcome = useCallback(() => {
    localStorage.setItem(STORAGE_KEY_WELCOME_DISMISSED, 'true');
    setShouldShow(false);
  }, []);

  const resetWelcome = useCallback(() => {
    setShouldShow(true);
  }, []);

  return { shouldShow, dismissWelcome, resetWelcome };
};
