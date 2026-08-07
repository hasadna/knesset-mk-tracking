import React, { useMemo } from 'react';
import { PartyGroup, MKMember } from '../types';
import { hexToRgba } from '../utils/formatters';
import { MKCard } from './MKCard';

interface PartyCardProps {
  party: PartyGroup;
  partyColor: string;
  selectedTopic: string;
  imageFor: (member: MKMember) => string;
  onSelectMember: (memberName: string) => void;
  onPrefetchMember: (member: MKMember) => Promise<void>;
}

function getPartySizeClass(seats: number): string {
  if (seats >= 20) return 'large';
  if (seats >= 8) return 'medium';
  return 'small';
}

export const PartyCard: React.FC<PartyCardProps> = React.memo(({
  party,
  partyColor,
  selectedTopic,
  imageFor,
  onSelectMember,
  onPrefetchMember,
}) => {
  const visibleCount = party.members.length;
  const totalSeats = party.seats;

  const sortedMembers = useMemo(
    () =>
      [...party.members].sort(
        (a, b) => (b.postCount || 0) - (a.postCount || 0) || a.name.localeCompare(b.name, 'he')
      ),
    [party.members]
  );

  return (
    <section
      className={`party-island ${getPartySizeClass(party.seats)}`}
      style={{
        '--party-color': partyColor,
        '--party-tint': hexToRgba(partyColor, 0.08),
      } as React.CSSProperties}
    >
      <header className="party-header">
        <div className="party-title-wrap">
          <h2 className="party-title">{party.name}</h2>
          <div className="party-subtitle">
            {party.current
              ? `${totalSeats} מושבים · ${party.status}`
              : 'אישים שאינם חברי הכנסת המכהנים עם מידע במאגר'}
          </div>
        </div>
        <div className="party-count">{visibleCount}</div>
      </header>

      <div className="member-cloud">
        {sortedMembers.map((member) => (
          <MKCard
            key={member.key}
            member={member}
            selectedTopic={selectedTopic}
            partyColor={partyColor}
            imageSrc={imageFor(member)}
            onSelect={onSelectMember}
            onPrefetch={onPrefetchMember}
          />
        ))}
      </div>
    </section>
  );
});

PartyCard.displayName = 'PartyCard';

