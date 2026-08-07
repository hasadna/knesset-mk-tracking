import React, { useState, useRef, useEffect, useLayoutEffect } from 'react';
import ReactDOM from 'react-dom';
import { InfoIcon, XIcon } from './icons';

interface SourcesInfoButtonProps {
  size?: number;
}

export const SourcesInfoButton: React.FC<SourcesInfoButtonProps> = ({ size = 14 }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [isPinned, setIsPinned] = useState(false);
  const [popoverStyle, setPopoverStyle] = useState<React.CSSProperties>({});
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const handleMouseEnter = () => setIsOpen(true);
  const handleMouseLeave = () => {
    if (!isPinned) setIsOpen(false);
  };

  const handleButtonClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isOpen && isPinned) {
      setIsPinned(false);
      setIsOpen(false);
    } else {
      setIsOpen(true);
      setIsPinned(true);
    }
  };

  const handleCloseClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsPinned(false);
    setIsOpen(false);
  };

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
        setIsOpen(false);
        setIsPinned(false);
      }
    };
    document.addEventListener('mousedown', handleOutsideClick);
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, [isOpen]);

  useLayoutEffect(() => {
    if (!isOpen || !buttonRef.current) return;

    const updatePosition = () => {
      if (!buttonRef.current) return;
      const btnRect = buttonRef.current.getBoundingClientRect();
      const padding = 12;
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;

      const popoverWidth = Math.min(360, viewportWidth - padding * 2);
      let left = btnRect.right - popoverWidth;

      if (left < padding) left = padding;
      if (left + popoverWidth > viewportWidth - padding) {
        left = viewportWidth - padding - popoverWidth;
      }

      const spaceBelow = viewportHeight - btnRect.bottom - padding;
      const spaceAbove = btnRect.top - padding;

      let top = btnRect.bottom + 6;
      let maxHeight = Math.min(420, spaceBelow);

      if (spaceBelow < 200 && spaceAbove > spaceBelow) {
        maxHeight = Math.min(420, spaceAbove - 6);
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

  const popoverContent = isOpen ? (
    <div
      ref={popoverRef}
      className="issue-helper-popover portal-popover"
      style={popoverStyle}
      role="tooltip"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="issue-helper-header">
        <div className="issue-helper-title-wrap">
          <span className="issue-helper-badge">בחירת ראיות וציוצים</span>
          <h4 className="issue-helper-title">אופן בחירת הציוצים התומכים</h4>
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

      <div className="issue-helper-body">
        <div className="issue-helper-section">
          <div className="issue-helper-prompt-prefix">
            כיצד נבחרים הציוצים המוצגים במאגר?
          </div>
          <div className="issue-helper-prompt-text">
            הציוצים התומכים נבחרים באופן אוטומטי מתוך מאגר הציוצים של חבר הכנסת אך ורק לפי הדמיון הסמנטי (Cosine Similarity) הגבוה ביותר להגדרת הנושא, ועד ל-8 ציוצים לכל היותר (עם סף דמיון מינימלי של 0.20).
          </div>
        </div>

        <div className="issue-helper-section">
          <div className="issue-helper-section-title">קריטריוני סינון נוספים</div>
          <p className="issue-helper-desc">
            לא מופעלים שום קריטריונים נוספים לבחירת הציוצים – המערכת מדרגת אותם אך ורק לפי ציון הדמיון הסמנטי המרבי שנמדד מול הנושא, ומעבירה את ה-8 המובילים לניתוח.
          </p>
        </div>
      </div>
    </div>
  ) : null;

  return (
    <div
      ref={containerRef}
      className={`issue-helper-wrap ${isOpen ? 'is-open' : ''} ${isPinned ? 'is-pinned' : ''}`}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <button
        ref={buttonRef}
        type="button"
        className={`issue-helper-btn ${isOpen ? 'active' : ''} ${isPinned ? 'pinned' : ''}`}
        onClick={handleButtonClick}
        aria-label="מידע על בחירת הציוצים התומכים"
        aria-expanded={isOpen}
        title={isPinned ? 'לחץ לסגירה' : 'הרחף לצפייה באלגוריתם בחירת הציוצים'}
      >
        <InfoIcon size={size} />
      </button>

      {isOpen && ReactDOM.createPortal(popoverContent, document.body)}
    </div>
  );
};
