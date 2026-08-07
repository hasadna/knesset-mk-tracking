import React, { useMemo, useState, useRef } from 'react';
import { MKMember, Topic } from '../types';

interface PartyScoreAxisProps {
  members: MKMember[];
  topic: Topic;
  partyColors: Map<string, string>;
  selectedParty?: string;
  onPartySelect?: (party: string) => void;
  onSelectMember?: (memberName: string) => void;
  imageFor?: (member: MKMember) => string | undefined;
}

interface MemberRating {
  member: MKMember;
  rating: number;
}

const SCORE_MIN = 1;
const SCORE_MAX = 5;

function getScoreColor(score: number): string {
  // Map score [1..5] to an abstract, non-judgmental spectrum (Deep Indigo -> Slate -> Cyan Teal)
  const normalized = Math.max(0, Math.min(1, (score - SCORE_MIN) / (SCORE_MAX - SCORE_MIN)));
  const hue = 235 - normalized * 60;
  const lightness = 50 - normalized * 6;
  return `hsl(${hue.toFixed(0)}, 70%, ${lightness.toFixed(0)}%)`;
}

function getTruncatedPartyName(name: string): string {
  if (!name) return '';
  const words = name.trim().split(/\s+/).slice(0, 2);
  const textWords = words
    .map((w) => w.replace(/^[\s\-–—.:,;]+|[\s\-–—.:,;]+$/g, ''))
    .filter((w) => w.length > 0);

  if (textWords.length === 0) return name;
  return textWords.join(' ');
}

export const PartyScoreAxis: React.FC<PartyScoreAxisProps> = ({
  members,
  topic,
  partyColors,
  selectedParty = 'all',
  onPartySelect,
  onSelectMember,
  imageFor,
}) => {
  const [activeParty, setActiveParty] = useState<string | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number } | null>(null);

  const [activeTick, setActiveTick] = useState<{ tick: number; meaning: string } | null>(null);
  const [tickTooltipPos, setTickTooltipPos] = useState<{ x: number; y: number } | null>(null);

  const panelRef = useRef<HTMLDivElement>(null);
  const closeTimerRef = useRef<NodeJS.Timeout | null>(null);

  const clearCloseTimer = () => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };

  const handleMouseEnterMarker = (partyName: string, event: React.MouseEvent<HTMLElement>) => {
    clearCloseTimer();
    setActiveTick(null);
    setTickTooltipPos(null);
    setActiveParty(partyName);
    const rect = event.currentTarget.getBoundingClientRect();
    const panelRect = panelRef.current?.getBoundingClientRect();
    if (panelRect) {
      const centerX = rect.left + rect.width / 2 - panelRect.left;
      const tooltipX = Math.max(12, Math.min(centerX - 140, panelRect.width - 292));
      const tooltipY = rect.bottom - panelRect.top + 8;

      setTooltipPos({
        x: tooltipX,
        y: tooltipY,
      });
    }
  };

  const handleMouseLeaveMarker = () => {
    clearCloseTimer();
    closeTimerRef.current = setTimeout(() => {
      setActiveParty(null);
      setTooltipPos(null);
      setActiveTick(null);
      setTickTooltipPos(null);
    }, 150);
  };

  const handleMouseEnterTick = (tick: number, meaning: string, event: React.MouseEvent<HTMLElement>) => {
    clearCloseTimer();
    setActiveParty(null);
    setTooltipPos(null);
    setActiveTick({ tick, meaning });
    const rect = event.currentTarget.getBoundingClientRect();
    const panelRect = panelRef.current?.getBoundingClientRect();
    if (panelRect) {
      const centerX = rect.left + rect.width / 2 - panelRect.left;
      const tooltipX = Math.max(12, Math.min(centerX - 100, panelRect.width - 220));
      const tooltipY = rect.top - panelRect.top - 42;

      setTickTooltipPos({
        x: tooltipX,
        y: tooltipY,
      });
    }
  };

  const partyScores = useMemo(() => {
    const grouped = new Map<string, MemberRating[]>();

    members
      .filter((member) => member.current)
      .forEach((member) => {
        const rating = member.ratings?.[topic.id];
        if (rating == null || rating < SCORE_MIN || rating > SCORE_MAX) return;
        const list = grouped.get(member.party) ?? [];
        list.push({ member, rating });
        grouped.set(member.party, list);
      });

    const allScores = [...grouped.entries()].map(([party, memberList]) => {
      memberList.sort((a, b) => b.rating - a.rating || a.member.name.localeCompare(b.member.name, 'he'));
      const total = memberList.reduce((sum, item) => sum + item.rating, 0);
      const score = total / memberList.length;
      return {
        party,
        score,
        memberCount: memberList.length,
        color: getScoreColor(score),
        members: memberList,
      };
    });

    // Sort by score descending (highest to lowest) for clear vertical list view
    allScores.sort((a, b) => b.score - a.score || a.party.localeCompare(b.party, 'he'));

    return allScores;
  }, [members, partyColors, topic.id]);

  const activePartyData = useMemo(() => {
    if (!activeParty) return null;
    return partyScores.find((p) => p.party === activeParty) || null;
  }, [activeParty, partyScores]);

  if (!partyScores.length) return null;

  const scale = topic.ratingScale ?? {};
  const lowLabel = scale[String(SCORE_MIN)] ?? '1';
  const highLabel = scale[String(SCORE_MAX)] ?? '5';

  const handleMarkerClick = (partyName: string) => {
    if (!onPartySelect) return;
    if (selectedParty === partyName) {
      onPartySelect('all');
    } else {
      onPartySelect(partyName);
    }
  };

  return (
    <section
      ref={panelRef}
      className="party-score-panel"
      aria-labelledby="party-score-title"
    >
      <header className="party-score-header">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
          <h2 id="party-score-title">
            מפת עמדות המפלגות: {topic.title}
          </h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <span
              className="party-score-ai-disclaimer"
              title="ניתוח הדירוג נוצר באמצעות בינה מלאכותית ועלול להכיל טעויות"
            >
              <span className="ai-badge-chip">AI</span>
              <span>עלול לטעות</span>
            </span>
            {selectedParty !== 'all' && onPartySelect && (
              <button
                type="button"
                className="party-score-filter-chip"
                onClick={() => onPartySelect('all')}
                title="לחץ לביטול הסינון"
              >
                <span>סינון פעיל: {selectedParty}</span>
                <span aria-hidden="true">✕</span>
              </button>
            )}
          </div>
        </div>
        <p>ממוצע ציוני חברי הכנסת בסולם 1 עד 5. שורות המפלגות ממוינות לפי ציון; רחף לצפייה בפירוט.</p>
      </header>

      {/* Flanking Side Labels Layout: Label of 1 (Right) | GRAPH BOX | Label of 5 (Left) */}
      <div
        className="party-score-axis-wrapper"
        role="region"
        aria-label={`ציוני המפלגות בנושא ${topic.title}, בסולם 1 עד 5`}
        onMouseLeave={handleMouseLeaveMarker}
      >
        {/* Background Connecting Axis Line Spanning Full Width (Reversed & Broader) */}
        <div className="party-axis-bg-connector" aria-hidden="true">
          <div className="party-axis-bg-arrow-left">
            <svg width="16" height="18" viewBox="0 0 16 18" fill="none">
              <path d="M4 2L12 9L4 16" stroke="hsl(235, 70%, 50%)" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <div className="party-axis-bg-line" />
          <div className="party-axis-bg-arrow-right">
            <svg width="16" height="18" viewBox="0 0 16 18" fill="none">
              <path d="M12 2L4 9L12 16" stroke="hsl(175, 70%, 44%)" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
        </div>

        {/* Physical Right: Label of 1 (RTL Start) */}
        <div
          className="party-axis-flank-text-label is-right"
          title={`דרגה 1: ${lowLabel}`}
        >
          <span className="party-axis-flank-text">{lowLabel}</span>
        </div>

        {/* Physical Center: GRAPH BOX */}
        <div className="party-axis-graph-box">
          {/* Ticks 1..5 Header */}
          <div className="party-row-ticks-header">
            <div className="party-row-ticks-inner">
              {[1, 2, 3, 4, 5].map((tick) => {
                const meaning = scale[String(tick)] || `דרגה ${tick} מתוך 5`;
                return (
                  <span
                    key={tick}
                    className="party-row-header-tick"
                    style={{
                      right: `${((tick - SCORE_MIN) / (SCORE_MAX - SCORE_MIN)) * 100}%`,
                      color: getScoreColor(tick),
                    }}
                    onMouseEnter={(e) => handleMouseEnterTick(tick, meaning, e)}
                    onMouseMove={(e) => handleMouseEnterTick(tick, meaning, e)}
                    onMouseLeave={handleMouseLeaveMarker}
                    title={`דרגה ${tick}: ${meaning}`}
                    aria-label={`דרגה ${tick}: ${meaning}`}
                  >
                    {tick}
                  </span>
                );
              })}
            </div>
          </div>

          {/* Per-Party Horizontal Lines */}
          <div className="party-rows-list">
            {partyScores.map((item) => {
              const isSelected = selectedParty === item.party;
              const isDimmed = selectedParty !== 'all' && !isSelected;
              const xPct = ((item.score - SCORE_MIN) / (SCORE_MAX - SCORE_MIN)) * 100;
              const truncatedName = getTruncatedPartyName(item.party);

              return (
                <div
                  key={item.party}
                  className={`party-score-row ${isSelected ? 'is-selected' : ''} ${isDimmed ? 'is-dimmed' : ''}`}
                  onClick={() => handleMarkerClick(item.party)}
                  onMouseEnter={(e) => handleMouseEnterMarker(item.party, e)}
                  onMouseMove={(e) => handleMouseEnterMarker(item.party, e)}
                  onMouseLeave={handleMouseLeaveMarker}
                  aria-label={`${item.party}: ציון ממוצע ${item.score.toFixed(1)}`}
                >
                  <div className="party-score-row-inner">
                    <div className="party-score-row-line" />
                    <div
                      className="party-score-row-marker-pill"
                      style={{
                        right: `${xPct}%`,
                        backgroundColor: item.color,
                      }}
                      title={`${item.party}: ${item.score.toFixed(1)}`}
                    >
                      <span className="party-score-pill-name">{truncatedName}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Physical Left: Label of 5 (RTL End) */}
        <div
          className="party-axis-flank-text-label is-left"
          title={`דרגה 5: ${highLabel}`}
        >
          <span className="party-axis-flank-text">{highLabel}</span>
        </div>
      </div>

      {/* Hover Scale Tick Popover */}
      {activeTick && tickTooltipPos && (
        <div
          className="party-score-tick-popover"
          style={{
            left: `${tickTooltipPos.x}px`,
            top: `${tickTooltipPos.y}px`,
          }}
          onMouseEnter={clearCloseTimer}
          onMouseLeave={handleMouseLeaveMarker}
        >
          <span
            className="party-score-tick-badge"
            style={{ backgroundColor: getScoreColor(activeTick.tick) }}
          >
            דרגה {activeTick.tick}
          </span>
          <span className="party-score-tick-text">{activeTick.meaning}</span>
        </div>
      )}

      {/* Hover/Focus Detail Popover */}
      {activePartyData && tooltipPos && (
        <div
          className="party-score-tooltip"
          style={{
            left: `${tooltipPos.x}px`,
            top: `${tooltipPos.y}px`,
          }}
          onMouseEnter={clearCloseTimer}
          onMouseLeave={handleMouseLeaveMarker}
        >
          <div className="party-score-tooltip-header">
            <div className="party-score-tooltip-title">
              <span
                style={{
                  display: 'inline-block',
                  width: '10px',
                  height: '10px',
                  borderRadius: '50%',
                  backgroundColor: activePartyData.color,
                }}
              />
              <span>{activePartyData.party}</span>
            </div>
            <span
              className="party-score-tooltip-badge"
              style={{ backgroundColor: activePartyData.color }}
            >
              {activePartyData.score.toFixed(1)}
            </span>
          </div>

          <div className="party-score-tooltip-members">
            {activePartyData.members.map(({ member, rating }) => {
              const imgUrl = imageFor?.(member);
              return (
                <div
                  key={member.name}
                  className="party-score-member-item"
                  onClick={() => {
                    if (onSelectMember) {
                      onSelectMember(member.name);
                      setActiveParty(null);
                    }
                  }}
                  title={`לחץ לצפייה בפרופיל של ${member.name}`}
                >
                  <div className="party-score-member-info">
                    {imgUrl ? (
                      <img
                        src={imgUrl}
                        alt={member.name}
                        className="party-score-member-avatar"
                      />
                    ) : (
                      <div className="party-score-member-avatar" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '10px', fontWeight: 'bold' }}>
                        {member.name.charAt(0)}
                      </div>
                    )}
                    <span className="party-score-member-name">{member.name}</span>
                  </div>
                  <span className="party-score-member-rating">{rating.toFixed(1)}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
};
