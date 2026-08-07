import React, { useState } from 'react';
import { MKMember, TopicStatus } from '../types';
import { initials, STATUS_EXPLANATIONS, STATUS_LABELS } from '../utils/formatters';

interface MKCardProps {
  member: MKMember;
  selectedTopic: string;
  partyColor: string;
  imageSrc: string;
  onSelect: (memberName: string) => void;
  onPrefetch: (member: MKMember) => Promise<void>;
}

function hasAnyOpinion(member: MKMember): boolean {
  if (!member.hasData || !member.coverage) return false;
  return Object.values(member.coverage).some((val) => val === 'strong' || val === 'partial');
}

function getStatusForMember(member: MKMember, selectedTopic: string): TopicStatus {
  if (!member.hasData) return 'none';
  if (selectedTopic === 'all') {
    return hasAnyOpinion(member) ? 'strong' : 'none';
  }
  return member.coverage[selectedTopic] || 'none';
}

function isRelevantForMember(member: MKMember, selectedTopic: string): boolean {
  if (!member.hasData) return false;
  if (selectedTopic === 'all') {
    return hasAnyOpinion(member);
  }
  const status = getStatusForMember(member, selectedTopic);
  return status === 'strong' || status === 'partial';
}

function getStateLabel(member: MKMember, selectedTopic: string): string {
  if (selectedTopic === 'all') {
    return member.hasData ? `${member.postCount} ציוצים` : 'אין מידע';
  }
  const status = getStatusForMember(member, selectedTopic);
  return STATUS_LABELS[status];
}

export const MKCard: React.FC<MKCardProps> = React.memo(({
  member,
  selectedTopic,
  partyColor,
  imageSrc,
  onSelect,
  onPrefetch,
}) => {
  const [imgLoaded, setImgLoaded] = useState<boolean>(false);
  const [imgError, setImgError] = useState<boolean>(false);

  const relevant = isRelevantForMember(member, selectedTopic);
  const status = getStatusForMember(member, selectedTopic);

  const statusColor =
    selectedTopic === 'all'
      ? partyColor
      : status === 'strong'
      ? 'var(--strong)'
      : status === 'partial'
      ? 'var(--partial)'
      : 'var(--none)';

  const stateLabel = getStateLabel(member, selectedTopic);
  const hasPhoto = Boolean(imageSrc) && !imgError;
  const isStillLoading = !imageSrc && Boolean(member.wikiTitle) && !imgError;

  const prefetchTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const handlePointerEnter = React.useCallback(() => {
    if (prefetchTimerRef.current) {
      clearTimeout(prefetchTimerRef.current);
    }
    prefetchTimerRef.current = setTimeout(() => {
      void onPrefetch(member).catch(() => undefined);
    }, 150);
  }, [member, onPrefetch]);

  const handlePointerLeave = React.useCallback(() => {
    if (prefetchTimerRef.current) {
      clearTimeout(prefetchTimerRef.current);
      prefetchTimerRef.current = null;
    }
  }, []);

  const handleFocus = React.useCallback(() => {
    void onPrefetch(member).catch(() => undefined);
  }, [member, onPrefetch]);

  return (
    <button
      className={`member-node ${relevant ? '' : 'dimmed'} ${selectedTopic === 'all' ? 'no-topic' : ''}`}
      type="button"
      data-tour="mk-card"
      data-member-name={member.name}
      style={{
        '--party-color': partyColor,
        '--node-status': statusColor,
      } as React.CSSProperties}
      aria-label={`${member.name}, ${member.party}, ${stateLabel}`}
      aria-haspopup="dialog"
      aria-controls="profileDrawer"
      onPointerEnter={handlePointerEnter}
      onPointerLeave={handlePointerLeave}
      onFocus={handleFocus}
      onClick={() => onSelect(member.name)}
    >
      <div className="avatar-wrap">
        <div
          className={`avatar ${hasPhoto || imgLoaded ? 'has-photo' : ''} ${
            isStillLoading && !imgLoaded ? 'loading' : 'loaded'
          }`}
        >
          <div className="fallback">{initials(member.name)}</div>
          {imageSrc && !imgError && (
            <img
              className={imgLoaded ? 'loaded' : ''}
              src={imageSrc}
              alt={`תמונת דיוקן של ${member.name}`}
              onLoad={() => setImgLoaded(true)}
              onError={() => setImgError(true)}
            />
          )}
        </div>
        {!member.current && <span className="former-dot">לשעבר</span>}
      </div>

      <span className="member-name">{member.name}</span>
      <span
        className={`member-state ${status}`}
        data-tooltip={STATUS_EXPLANATIONS[status]}
      >
        {stateLabel}
      </span>
    </button>
  );
});

MKCard.displayName = 'MKCard';

