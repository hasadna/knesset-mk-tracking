import React from 'react';
import { PartyGroup, MKMember } from '../types';
import { PartyCard } from './PartyCard';

interface PartyBoardProps {
  parties: PartyGroup[];
  partyColors: Map<string, string>;
  selectedTopic: string;
  imageFor: (member: MKMember) => string;
  onSelectMember: (memberName: string) => void;
  onPrefetchMember: (member: MKMember) => Promise<void>;
  isEmpty: boolean;
}

export const PartyBoard: React.FC<PartyBoardProps> = ({
  parties,
  partyColors,
  selectedTopic,
  imageFor,
  onSelectMember,
  onPrefetchMember,
  isEmpty,
}) => {
  return (
    <section id="partyBoard" className="party-board" aria-label="חברי הכנסת לפי מפלגות" data-tour="party-board">
      {isEmpty ? (
        <div className="empty-state">לא נמצאו חברי כנסת התואמים למסננים.</div>
      ) : (
        parties.map((party) => {
          const color = partyColors.get(party.name) || '#607d8b';
          return (
            <PartyCard
              key={party.name}
              party={party}
              partyColor={color}
              selectedTopic={selectedTopic}
              imageFor={imageFor}
              onSelectMember={onSelectMember}
              onPrefetchMember={onPrefetchMember}
            />
          );
        })
      )}
    </section>
  );
};
