import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { MKMember, Topic } from '../types';
import { IssueInfoButton } from './IssueInfoButton';

interface PartyScoreAxisProps {
  members: MKMember[];
  topic: Topic;
  selectedParty?: string;
  onPartySelect?: (party: string) => void;
  onSelectMember?: (memberName: string) => void;
  imageFor?: (member: MKMember) => string | undefined;
}

interface MemberRating {
  member: MKMember;
  rating: number;
}

type Popover =
  | { kind: 'party'; party: string }
  | { kind: 'tick'; tick: number; meaning: string };

const SCORE_MIN = 1;
const SCORE_MAX = 5;
const TICKS = [1, 2, 3, 4, 5];
const CLOSE_DELAY_MS = 150;
const POPOVER_GAP = 8;
const PANEL_EDGE = 12;

function getScoreColor(score: number): string {
  // Map score [1..5] to an abstract, non-judgmental spectrum (Deep Indigo -> Slate -> Cyan Teal).
  // Lightness runs 50% -> 28% so white marker text clears 4.5:1 across the whole ramp
  // (worst case 4.77:1 at score 4; the old 50% -> 44% bottomed out at 2.30:1).
  const normalized = Math.max(0, Math.min(1, (score - SCORE_MIN) / (SCORE_MAX - SCORE_MIN)));
  const hue = 235 - normalized * 60;
  const lightness = 50 - normalized * 22;
  return `hsl(${hue.toFixed(0)}, 70%, ${lightness.toFixed(0)}%)`;
}

function scoreToPercent(score: number): number {
  return ((score - SCORE_MIN) / (SCORE_MAX - SCORE_MIN)) * 100;
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
  selectedParty = 'all',
  onPartySelect,
  onSelectMember,
  imageFor,
}) => {
  const [popover, setPopover] = useState<Popover | null>(null);
  const [popoverPos, setPopoverPos] = useState<{ x: number; y: number } | null>(null);
  // Mirrors `popover` so openPopover can compare against the current target
  // without nesting a setState inside the setPopover updater (which must stay pure).
  const popoverStateRef = useRef<Popover | null>(null);

  const panelRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const anchorRef = useRef<HTMLElement | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const closePopover = useCallback(() => {
    clearCloseTimer();
    anchorRef.current = null;
    popoverStateRef.current = null;
    setPopover(null);
    setPopoverPos(null);
  }, [clearCloseTimer]);

  // Deferred close, so the pointer can travel from the anchor into the popover.
  const scheduleClose = useCallback(() => {
    clearCloseTimer();
    closeTimerRef.current = setTimeout(closePopover, CLOSE_DELAY_MS);
  }, [clearCloseTimer, closePopover]);

  const openPopover = useCallback(
    (next: Popover, anchor: HTMLElement) => {
      clearCloseTimer();
      anchorRef.current = anchor;
      // Re-opening the same target must not reset its measured position.
      const current = popoverStateRef.current;
      const sameTarget =
        !!current &&
        current.kind === next.kind &&
        ((current.kind === 'party' && next.kind === 'party' && current.party === next.party) ||
          (current.kind === 'tick' && next.kind === 'tick' && current.tick === next.tick));
      if (sameTarget) return;
      popoverStateRef.current = next;
      setPopover(next);
      setPopoverPos(null);
    },
    [clearCloseTimer]
  );

  useEffect(() => clearCloseTimer, [clearCloseTimer]);

  // A row spans the whole plot, so anchor its popover to the marker instead.
  const markerOf = (row: HTMLElement): HTMLElement =>
    row.querySelector<HTMLElement>('.party-score-row-marker-pill') ?? row;

  // Measure the rendered popover, then place it inside the panel: centred on its
  // anchor, flipped above when it would overflow the bottom, clamped on both axes.
  useLayoutEffect(() => {
    if (!popover || popoverPos) return;
    const anchor = anchorRef.current;
    const panel = panelRef.current;
    const pop = popoverRef.current;
    if (!anchor || !panel || !pop) return;

    const anchorRect = anchor.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const popRect = pop.getBoundingClientRect();

    const centerX = anchorRect.left + anchorRect.width / 2 - panelRect.left;
    const maxX = Math.max(PANEL_EDGE, panelRect.width - popRect.width - PANEL_EDGE);
    const x = Math.min(Math.max(centerX - popRect.width / 2, PANEL_EDGE), maxX);

    // Flip on the viewport, not the panel: a popover that fits the panel can
    // still open below the fold.
    const below = anchorRect.bottom - panelRect.top + POPOVER_GAP;
    const above = anchorRect.top - panelRect.top - popRect.height - POPOVER_GAP;
    const roomBelow = window.innerHeight - anchorRect.bottom - POPOVER_GAP - PANEL_EDGE;
    const roomAbove = anchorRect.top - POPOVER_GAP - PANEL_EDGE;
    const placeBelow = popRect.height <= roomBelow || roomAbove < popRect.height;

    const minY = PANEL_EDGE - panelRect.top;
    const maxY = window.innerHeight - panelRect.top - popRect.height - PANEL_EDGE;
    const y = Math.min(Math.max(placeBelow ? below : above, minY), Math.max(minY, maxY));

    setPopoverPos({ x, y });
  }, [popover, popoverPos]);

  useEffect(() => {
    if (!popover) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closePopover();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [popover, closePopover]);

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
  }, [members, topic.id]);

  const activePartyData = useMemo(() => {
    if (popover?.kind !== 'party') return null;
    return partyScores.find((p) => p.party === popover.party) || null;
  }, [popover, partyScores]);

  if (!partyScores.length) return null;

  const scale = topic.ratingScale ?? {};
  const lowLabel = scale[String(SCORE_MIN)] ?? '1';
  const highLabel = scale[String(SCORE_MAX)] ?? '5';

  const handleMarkerClick = (partyName: string) => {
    if (!onPartySelect) return;
    onPartySelect(selectedParty === partyName ? 'all' : partyName);
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
      </header>

      {/* Single-column plot. Each end is a tinted gutter carrying its pole
          wording; the track is inset past them so a marker at 1.0 or 5.0 never
          lands on the text. */}
      <div
        className="party-axis-graph-box"
        role="region"
        aria-label={`ציוני המפלגות בנושא ${topic.title}, בסולם 1 עד 5`}
        onMouseLeave={scheduleClose}
      >
        <div className="party-axis-gutter is-low">
          <span>{lowLabel}</span>
        </div>
        <div className="party-axis-gutter is-high">
          <span>{highLabel}</span>
        </div>

        <div className="party-row-ticks-header">
          <div className="party-row-ticks-inner">
            {TICKS.map((tick) => {
              const meaning = scale[String(tick)] || `דרגה ${tick} מתוך 5`;
              return (
                <button
                  key={tick}
                  type="button"
                  className="party-row-header-tick"
                  style={{ right: `${scoreToPercent(tick)}%` }}
                  onMouseEnter={(e) => openPopover({ kind: 'tick', tick, meaning }, e.currentTarget)}
                  onMouseLeave={scheduleClose}
                  onFocus={(e) => openPopover({ kind: 'tick', tick, meaning }, e.currentTarget)}
                  onBlur={scheduleClose}
                  aria-label={`דרגה ${tick}: ${meaning}`}
                >
                  {tick}
                </button>
              );
            })}
          </div>
        </div>

        <div className="party-rows-list">
          <div className="party-axis-gridlines" aria-hidden="true">
            {TICKS.map((tick) => (
              <i
                key={tick}
                className={tick === 3 ? 'is-mid' : undefined}
                style={{ right: `${scoreToPercent(tick)}%` }}
              />
            ))}
          </div>

          {partyScores.map((item) => {
            const isSelected = selectedParty === item.party;
            const isDimmed = selectedParty !== 'all' && !isSelected;
            const truncatedName = getTruncatedPartyName(item.party);

            return (
              <button
                key={item.party}
                type="button"
                className={`party-score-row ${isSelected ? 'is-selected' : ''} ${isDimmed ? 'is-dimmed' : ''}`}
                onClick={() => handleMarkerClick(item.party)}
                onMouseEnter={(e) => openPopover({ kind: 'party', party: item.party }, markerOf(e.currentTarget))}
                onMouseLeave={scheduleClose}
                onFocus={(e) => openPopover({ kind: 'party', party: item.party }, markerOf(e.currentTarget))}
                onBlur={scheduleClose}
                aria-pressed={isSelected}
                aria-label={`${item.party}: ציון ממוצע ${item.score.toFixed(1)} מתוך 5, ${item.memberCount} חברי כנסת`}
              >
                <span className="party-score-row-inner">
                  <span
                    className="party-score-row-marker-pill"
                    style={{
                      right: `${scoreToPercent(item.score)}%`,
                      backgroundColor: item.color,
                    }}
                  >
                    <span className="party-score-pill-name">{truncatedName}</span>
                  </span>
                </span>
              </button>
            );
          })}
        </div>

        {/* Narrow screens have no room for the gutters; the poles move inline. */}
        <div className="party-axis-poles-compact" aria-hidden="true">
          <span>{lowLabel}</span>
          <span>{highLabel}</span>
        </div>
      </div>

      <p className="party-score-footnote">
        <IssueInfoButton topic={topic}>
          <span className="ai-badge-chip">AI</span>
        </IssueInfoButton>
      </p>

      {popover?.kind === 'tick' && (
        <div
          ref={popoverRef}
          className="party-score-tick-popover"
          role="tooltip"
          style={{
            left: `${popoverPos?.x ?? 0}px`,
            top: `${popoverPos?.y ?? 0}px`,
            visibility: popoverPos ? 'visible' : 'hidden',
          }}
          onMouseEnter={clearCloseTimer}
          onMouseLeave={scheduleClose}
        >
          <span
            className="party-score-tick-badge"
            style={{ backgroundColor: getScoreColor(popover.tick) }}
          >
            דרגה {popover.tick}
          </span>
          <span className="party-score-tick-text">{popover.meaning}</span>
        </div>
      )}

      {activePartyData && (
        <div
          ref={popoverRef}
          className="party-score-tooltip"
          role="tooltip"
          style={{
            left: `${popoverPos?.x ?? 0}px`,
            top: `${popoverPos?.y ?? 0}px`,
            visibility: popoverPos ? 'visible' : 'hidden',
          }}
          onMouseEnter={clearCloseTimer}
          onMouseLeave={scheduleClose}
        >
          <div className="party-score-tooltip-header">
            <div className="party-score-tooltip-title">
              <span
                className="party-score-pill-dot"
                style={{ backgroundColor: activePartyData.color }}
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
                <button
                  key={member.name}
                  type="button"
                  className="party-score-member-item"
                  onClick={() => {
                    if (onSelectMember) {
                      onSelectMember(member.name);
                      closePopover();
                    }
                  }}
                  title={`לחץ לצפייה בפרופיל של ${member.name}`}
                >
                  <span className="party-score-member-info">
                    {imgUrl ? (
                      <img src={imgUrl} alt="" className="party-score-member-avatar" />
                    ) : (
                      <span className="party-score-member-avatar is-initial" aria-hidden="true">
                        {member.name.charAt(0)}
                      </span>
                    )}
                    <span className="party-score-member-name">{member.name}</span>
                  </span>
                  <span className="party-score-member-rating">{rating.toFixed(1)}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
};
