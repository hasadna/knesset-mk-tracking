import React, { useState } from 'react';
import { VoteItem, VoteSummary, VoteDecision } from '../types';
import { formatIsraeliDate } from '../utils/formatters';
import { ExternalLinkIcon } from './icons';

interface VoteChipsProps {
  votes?: VoteItem[];
  summary?: VoteSummary;
}

const DECISION_LABELS: Record<VoteDecision, string> = {
  for: 'בעד',
  against: 'נגד',
  abstain: 'נמנע',
  absent: 'נעדר',
};

const BillSummaryBox: React.FC<{ summary: string }> = ({ summary }) => {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);
  const isLong = summary.length > 110;

  return (
    <div className={`vote-bill-summary ${isExpanded ? 'expanded' : 'clamped'}`}>
      <div className="vote-bill-summary-header">
        <span className="ai-badge-chip">AI תמצית החוק</span>
        {isLong && (
          <button
            type="button"
            className="vote-summary-toggle-btn"
            onClick={() => setIsExpanded((prev) => !prev)}
          >
            {isExpanded ? 'הצג פחות ▲' : 'קרא עוד ▼'}
          </button>
        )}
      </div>
      <p className="vote-bill-summary-text">{summary}</p>
    </div>
  );
};

export const VoteChips: React.FC<VoteChipsProps> = ({ votes = [], summary }) => {
  const [expanded, setExpanded] = useState<boolean>(false);

  if (!votes || votes.length === 0) {
    return null;
  }

  const initialLimit = 4;
  const visibleVotes = expanded ? votes : votes.slice(0, initialLimit);
  const hasMore = votes.length > initialLimit;

  // Calculate summary counts if not explicitly provided
  const forCnt = summary?.forCount ?? votes.filter((v) => v.vote === 'for').length;
  const againstCnt = summary?.againstCount ?? votes.filter((v) => v.vote === 'against').length;
  const abstainCnt = summary?.abstainCount ?? votes.filter((v) => v.vote === 'abstain').length;
  const absentCnt = summary?.absentCount ?? votes.filter((v) => v.vote === 'absent').length;

  return (
    <section className="vote-chips-section">
      <div className="vote-section-header">
        <h4 className="vote-section-title">
          <span className="vote-icon" aria-hidden="true">🗳️</span> הצבעות בכנסת בנושא
        </h4>
        <div className="vote-summary-pills">
          {forCnt > 0 && <span className="vote-pill for">{forCnt} בעד</span>}
          {againstCnt > 0 && <span className="vote-pill against">{againstCnt} נגד</span>}
          {abstainCnt > 0 && <span className="vote-pill abstain">{abstainCnt} נמנע</span>}
          {absentCnt > 0 && <span className="vote-pill absent">{absentCnt} נעדר</span>}
        </div>
      </div>

      <div className="vote-cards-grid">
        {visibleVotes.map((vote) => {
          const decisionLabel = DECISION_LABELS[vote.vote] || vote.vote;
          const formattedDate = formatIsraeliDate(vote.date);
          const arenaLabel = vote.eventKind === 'committee' ? 'וועדה' : 'מליאה';

          return (
            <article key={vote.id} className={`vote-chip-card vote-${vote.vote}`}>
              <div className="vote-chip-header">
                <span className={`vote-decision-badge ${vote.vote}`}>
                  {decisionLabel}
                </span>
                <span className="vote-meta">
                  {arenaLabel} · {formattedDate}
                </span>
              </div>
              <p className="vote-title">{vote.title}</p>
              {vote.summary && <BillSummaryBox summary={vote.summary} />}
              {vote.documentUri && (
                <a
                  href={vote.documentUri}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="vote-doc-link"
                  title="צפייה במסמך הצעת החוק"
                >
                  למסמך החוק <ExternalLinkIcon size={12} />
                </a>
              )}
            </article>
          );
        })}
      </div>

      {hasMore && (
        <button
          type="button"
          className="vote-expand-btn"
          onClick={() => setExpanded((prev) => !prev)}
        >
          {expanded
            ? 'הצג פחות הצבעות ▲'
            : `הצג עוד ${votes.length - initialLimit} הצבעות ▼`}
        </button>
      )}
    </section>
  );
};
