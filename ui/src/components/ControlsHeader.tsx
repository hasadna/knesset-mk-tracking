import React, { useState, useEffect } from 'react';
import { Topic, SortMode, Theme } from '../types';
import { getTopicColor } from '../utils/formatters';
import { SearchIcon, FilterIcon, ChevronDownIcon, ChevronUpIcon, SunIcon, MoonIcon, HelpCircleIcon } from './icons';

interface ControlsHeaderProps {
  searchQuery: string;
  onSearchChange: (value: string) => void;
  selectedParty: string;
  onPartyChange: (party: string) => void;
  sortMode: SortMode;
  onSortModeChange: (mode: SortMode) => void;
  relevantOnly: boolean;
  onRelevantOnlyChange: (checked: boolean) => void;
  selectedTopic: string;
  onTopicSelect: (topicId: string) => void;
  topics: Topic[];
  parties: string[];
  theme: Theme;
  onToggleTheme: () => void;
  onOpenWelcome: () => void;
}

export const ControlsHeader: React.FC<ControlsHeaderProps> = ({
  searchQuery,
  onSearchChange,
  selectedParty,
  onPartyChange,
  sortMode,
  onSortModeChange,
  relevantOnly,
  onRelevantOnlyChange,
  selectedTopic,
  onTopicSelect,
  topics,
  parties,
  theme,
  onToggleTheme,
  onOpenWelcome,
}) => {
  const [isCollapsed, setIsCollapsed] = useState<boolean>(() => {
    return localStorage.getItem('mk_filters_collapsed') === 'true';
  });

  useEffect(() => {
    localStorage.setItem('mk_filters_collapsed', String(isCollapsed));
  }, [isCollapsed]);

  const toggleFilters = () => {
    setIsCollapsed((prev) => !prev);
  };

  return (
    <section className="controls" aria-label="כלי סינון" data-tour="controls-header">
      <div className="controls-primary-row">
        <div className="search-wrap">
          <span className="search-icon">
            <SearchIcon size={16} />
          </span>
          <input
            id="searchInput"
            className="control search"
            type="search"
            placeholder="חיפוש חבר כנסת או מפלגה…"
            aria-label="חיפוש חבר כנסת או מפלגה"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
          />
        </div>

        <button
          id="guideBtn"
          className="control-btn guide-btn"
          aria-label="מדריך לשימוש והסברים"
          data-tooltip="פתיחת מסך הסבר וסיור מודרך"
          onClick={onOpenWelcome}
          type="button"
        >
          <HelpCircleIcon size={16} /> <span className="guide-btn-text">מדריך</span>
        </button>

        <button
          id="toggleFiltersBtn"
          className={`control-btn toggle-filters-btn ${!isCollapsed ? 'active' : ''}`}
          aria-expanded={!isCollapsed}
          data-tooltip="הצגה/הסתרה של פילטרים מתקדמים"
          onClick={toggleFilters}
          type="button"
        >
          <FilterIcon size={15} /> פילטרים{' '}
          <span id="filterToggleArrow">
            {isCollapsed ? <ChevronDownIcon size={14} /> : <ChevronUpIcon size={14} />}
          </span>
        </button>

        <button
          id="themeToggleBtn"
          className="control-btn theme-btn"
          aria-label="החלפת ערכת נושא"
          data-tooltip="החלפה בין מצב בהיר לכהה"
          onClick={onToggleTheme}
          type="button"
        >
          <span id="themeToggleIcon">
            {theme === 'dark' ? <SunIcon size={15} /> : <MoonIcon size={15} />}
          </span>{' '}
          <span id="themeToggleText">{theme === 'dark' ? 'מצב בהיר' : 'מצב כהה'}</span>
        </button>
      </div>

      <div
        id="filtersCollapsiblePanel"
        className={`filters-collapsible-panel ${isCollapsed ? 'collapsed' : ''}`}
      >
        <div className="secondary-controls-grid">
          <select
            id="partyFilter"
            className="control"
            aria-label="סינון לפי מפלגה"
            value={selectedParty}
            onChange={(e) => onPartyChange(e.target.value)}
          >
            <option value="all">כל המפלגות</option>
            {parties.map((party) => (
              <option key={party} value={party}>
                {party}
              </option>
            ))}
          </select>

          <select
            id="sortMode"
            className="control"
            aria-label="סידור מפלגות"
            value={sortMode}
            onChange={(e) => onSortModeChange(e.target.value as SortMode)}
          >
            <option value="seats">סידור לפי גודל הסיעה</option>
            <option value="alpha">סידור אלפביתי</option>
          </select>

          <label className="toggle">
            <input
              id="relevantOnly"
              type="checkbox"
              checked={relevantOnly}
              onChange={(e) => onRelevantOnlyChange(e.target.checked)}
            />
            הצג רק רלוונטיים
          </label>
        </div>

        <div className="topic-strip-label">
          הדגשה לפי נושא · הניתוח מבוסס על ציוצים ממאגר הנתונים
        </div>
        <div id="topicStrip" className="topic-strip">
          <button
            className={`topic-chip ${selectedTopic === 'all' ? 'active' : ''}`}
            type="button"
            data-topic-id="all"
            aria-pressed={selectedTopic === 'all'}
            onClick={() => onTopicSelect('all')}
          >
            כל הנושאים
          </button>
          {topics.map((topic) => {
            const color = getTopicColor(topic.id);
            const isActive = selectedTopic === topic.id;
            return (
              <button
                key={topic.id}
                className={`topic-chip ${isActive ? 'active' : ''}`}
                type="button"
                data-topic-id={topic.id}
                aria-pressed={isActive}
                style={{
                  '--topic-color': color,
                  borderColor: isActive ? color : undefined,
                  backgroundColor: isActive ? color : undefined,
                  color: isActive ? '#ffffff' : undefined,
                } as React.CSSProperties}
                onClick={() => onTopicSelect(topic.id)}
              >
                {topic.title}
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
};
