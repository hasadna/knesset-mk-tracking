import React, { useState, useRef, useEffect, useLayoutEffect } from 'react';
import ReactDOM from 'react-dom';
import { Drawer } from 'vaul';
import { Topic } from '../types';
import { InfoIcon, XIcon } from './icons';

interface IssueInfoButtonProps {
  topic: Topic | null | undefined;
  modelVersion?: string | null;
  size?: number;
  children?: React.ReactNode;
}

export const IssueInfoButton: React.FC<IssueInfoButtonProps> = ({ topic, modelVersion, size = 15, children }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const [isPinned, setIsPinned] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [popoverStyle, setPopoverStyle] = useState<React.CSSProperties>({});
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  if (!topic) return null;

  const closePopover = () => {
    if (isMobile) {
      setIsOpen(false);
      setIsPinned(false);
      return;
    }
    setIsClosing(true);
    setTimeout(() => {
      setIsOpen(false);
      setIsClosing(false);
      setIsPinned(false);
    }, 250); // Matches CSS animation duration
  };

  const handleMouseEnter = () => {
    if (isMobile || isClosing) return;
    setIsOpen(true);
  };

  const handleMouseLeave = () => {
    if (!isPinned) {
      closePopover();
    }
  };

  const handleButtonClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isOpen && isPinned) {
      closePopover();
    } else {
      if (isClosing) return;
      setIsOpen(true);
      setIsPinned(true);
    }
  };

  const handleCloseClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    closePopover();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.stopPropagation();
      e.preventDefault();
      if (isOpen && isPinned) {
        closePopover();
      } else {
        if (isClosing) return;
        setIsOpen(true);
        setIsPinned(true);
      }
    } else if (e.key === 'Escape' && isOpen) {
      e.stopPropagation();
      closePopover();
    }
  };

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth <= 650);
    };
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // Close popover when clicking anywhere outside
  useEffect(() => {
    if (!isOpen) return;
    const handleOutsideClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        containerRef.current &&
        !containerRef.current.contains(target) &&
        popoverRef.current &&
        !popoverRef.current.contains(target)
      ) {
        closePopover();
      }
    };
    document.addEventListener('mousedown', handleOutsideClick);
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, [isOpen]);

  // Compute fixed viewport position reliably using portal
  useLayoutEffect(() => {
    if (!isOpen || !buttonRef.current) return;

    const updatePosition = () => {
      if (!buttonRef.current) return;
      const btnRect = buttonRef.current.getBoundingClientRect();
      const padding = 12;
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;

      // Desired popover width
      const popoverWidth = Math.min(380, viewportWidth - padding * 2);

      // RTL alignment: align right edge of popover with right edge of button
      let left = btnRect.right - popoverWidth;

      // Clamp left boundary so popover doesn't overflow left or right screen edge
      if (left < padding) left = padding;
      if (left + popoverWidth > viewportWidth - padding) {
        left = viewportWidth - padding - popoverWidth;
      }

      // Vertical placement decision
      const spaceBelow = viewportHeight - btnRect.bottom - padding;
      const spaceAbove = btnRect.top - padding;

      let top = btnRect.bottom + 6;
      let maxHeight = Math.min(540, spaceBelow);

      if (spaceBelow < 240 && spaceAbove > spaceBelow) {
        // Place above button if space below is tight
        maxHeight = Math.min(540, spaceAbove - 6);
        top = Math.max(padding, btnRect.top - 6 - maxHeight);
      }

      setPopoverStyle({
        position: 'fixed',
        top: `${top}px`,
        left: `${left}px`,
        width: `${popoverWidth}px`,
        maxHeight: `${maxHeight}px`,
        zIndex: 2147483647,
      });
    };

    updatePosition();
    window.addEventListener('resize', updatePosition);
    window.addEventListener('scroll', updatePosition, true);
    return () => {
      window.removeEventListener('resize', updatePosition);
      window.removeEventListener('scroll', updatePosition, true);
    };
  }, [isOpen]);

  const rawPrompt = topic.promptForSocialPostSimilarity || topic.description || '';

  const renderContent = (isVaul: boolean = false) => (
    <>
      {!isVaul && (
        <div className="issue-helper-header">
          <div className="issue-helper-title-wrap">
            <span className="issue-helper-badge">הגדרת נושא ומדד</span>
            <h4 className="issue-helper-title">{topic.title}</h4>
            {topic.group && <span className="issue-helper-group">{topic.group}</span>}
          </div>
          <button
            type="button"
            className="issue-helper-close"
            onClick={handleCloseClick}
            aria-label="סגירה"
          >
            <XIcon size={14} />
          </button>
        </div>
      )}

      {isVaul && (
        <div className="vaul-header">
          <div className="vaul-handle" />
          <div className="vaul-title-wrap">
            <span className="issue-helper-badge">הגדרת נושא ומדד</span>
            <h4 className="issue-helper-title">{topic.title}</h4>
            {topic.group && <span className="issue-helper-group">{topic.group}</span>}
          </div>
        </div>
      )}

      <div className="issue-helper-body">
        <div className="issue-helper-section">
          <div className="issue-helper-section-title">הפרומפט</div>
          ביקשנו ממודל AI לסכם את הציוצים שמצאנו 
          <div className="issue-helper-prompt-prefix">
            הפרומפט שהשתמשנו בו כדי לסכם את תוכן הציוצים הוא:
          </div>
          <div className="issue-helper-prompt-text" dir="auto">
            {rawPrompt}
          </div>
          (כל הציוצים נשלחו יחד, תחת התיאור הזה ובעזרת פרומפט מערכת נוסף שלא משתנה בין נושאים)
        </div>

        {topic.description && (
          <div className="issue-helper-section">
            <div className="issue-helper-section-title">מהות הנושא והרעיון המרכזי</div>
            <p className="issue-helper-desc" dir="auto">{topic.description}</p>
          </div>
        )}

        <div className="issue-helper-section">
          <div className="issue-helper-section-title">הסבר על סולם הדירוג</div>
          <p className="issue-helper-rating-note">
            עמדת חבר הכנסת מוערכת על גבי סולם רציף מ-1 עד 5. לקבלת ההגדרה והמשמעות המדויקת של כל דרגה בסולם, ניתן להרחיף מעל נקודות הדירוג בסרגל המדד שבכרטיס.
          </p>
        </div>

        {topic.contrast && (
          <div className="issue-helper-section">
            <div className="issue-helper-section-title">קונטרסט והגדרת הצירים</div>
            <p className="issue-helper-contrast" dir="auto">{topic.contrast}</p>
          </div>
        )}

        {topic.tags && topic.tags.length > 0 && (
          <div className="issue-helper-section">
            <div className="issue-helper-section-title">תגיות ונושאי משנה</div>
            <div className="issue-helper-tags">
              {topic.tags.map((tag, idx) => (
                <span key={idx} className="issue-helper-tag">{tag}</span>
              ))}
            </div>
          </div>
        )}

        {modelVersion && (
          <div className="issue-helper-section">
            <div className="issue-helper-section-title">מודל שפה (LLM) שביצע את הסיכום</div>
            <div className="issue-helper-tag" style={{ background: 'color-mix(in srgb, var(--accent) 15%, var(--surface-2))', color: 'var(--accent)', fontWeight: 700 }}>
              {modelVersion}
            </div>
          </div>
        )}
      </div>
    </>
  );

  const desktopPopoverContent = (isOpen && !isMobile) ? (
    <div
      ref={popoverRef}
      className={`issue-helper-popover portal-popover ${isClosing ? 'closing' : ''}`}
      style={popoverStyle}
      role="tooltip"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onClick={(e) => e.stopPropagation()}
    >
      {renderContent(false)}
    </div>
  ) : null;

  return (
    <div
      ref={containerRef}
      className={`issue-helper-wrap ${isOpen ? 'is-open' : ''} ${isPinned ? 'is-pinned' : ''}`}
    >
      <button
        ref={buttonRef}
        type="button"
        className={`issue-helper-btn ${isOpen ? 'active' : ''} ${isPinned ? 'pinned' : ''} ${children ? 'has-custom-trigger' : ''}`}
        onClick={handleButtonClick}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        onKeyDown={handleKeyDown}
        aria-label={`מידע על נושא: ${topic.title}`}
        aria-expanded={isOpen}
        title={isPinned ? 'לחץ לסגירה' : 'הרחף לצפייה, לחץ לקבע מפתח'}
      >
        {children ? children : <InfoIcon size={size} />}
      </button>

      {isMobile ? (
        <Drawer.Root open={isOpen} onOpenChange={(open) => {
          setIsOpen(open);
          if (open) setIsPinned(true);
          else setIsPinned(false);
        }}>
          <Drawer.Portal>
            <Drawer.Overlay className="vaul-overlay" />
            <Drawer.Content className="vaul-content">
              <div className="vaul-scroll-container">
                {renderContent(true)}
              </div>
            </Drawer.Content>
          </Drawer.Portal>
        </Drawer.Root>
      ) : (
        isOpen && ReactDOM.createPortal(
          <>
            <div className={`issue-helper-backdrop ${isClosing ? 'closing' : ''}`} onClick={closePopover} />
            {desktopPopoverContent}
          </>,
          document.body
        )
      )}
    </div>
  );
};
