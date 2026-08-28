import React, { useState, useMemo, useCallback } from 'react';
import { Routes, Route, useNavigate, useMatch } from 'react-router-dom';
import { SortMode, PartyGroup, TweetPost } from './types';
import { useMKData } from './hooks/useMKData';
import { useWikipediaImages } from './hooks/useWikipediaImages';
import { useTheme } from './hooks/useTheme';
import { useMKAnalysis } from './hooks/useMKAnalysis';
import { ControlsHeader } from './components/ControlsHeader';
import { StatusSummaryRow } from './components/StatusSummaryRow';
import { PartyBoard } from './components/PartyBoard';
import { PartyScoreAxis } from './components/PartyScoreAxis';
import { ProfileDrawer } from './components/ProfileDrawer';
import { SourceDialog } from './components/SourceDialog';
import { WelcomeModal, useWelcomeState } from './components/WelcomeModal';
import { WalkthroughTour, WalkthroughStepConfig } from './components/WalkthroughTour';
import { SiteHeader } from './components/SiteHeader';
import { SiteFooter } from './components/SiteFooter';

const NON_MK_CATEGORY = 'לא חברי כנסת';

export const App: React.FC = () => {
  const { roster, topics, partyInfo, partyColors, topicMap, loading, error } = useMKData();
  const { imageFor, resolveHighResImage } = useWikipediaImages(roster);
  const { theme, toggleTheme } = useTheme();
  const { loadEvidence, getTopicDetail, analysisMap, postMap } = useMKAnalysis();

  const navigate = useNavigate();
  const match = useMatch('/mk/:id');
  const selectedMemberKey = match?.params.id || null;

  const { shouldShow: isWelcomeOpen, dismissWelcome, resetWelcome } = useWelcomeState();
  const [isTourActive, setIsTourActive] = useState<boolean>(false);

  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedParty, setSelectedParty] = useState<string>('all');
  const [sortMode, setSortMode] = useState<SortMode>('seats');
  const [relevantOnly, setRelevantOnly] = useState<boolean>(false);
  const [selectedTopic, setSelectedTopic] = useState<string>('all');

  const [selectedSourceKey, setSelectedSourceKey] = useState<string | null>(null);

  // List of distinct parties from current MKs
  const partiesList = useMemo(() => {
    return [...new Set(roster.filter((m) => m.current).map((m) => m.party))];
  }, [roster]);

  // Helper functions for filtering MKs
  const hasAnyOpinion = useCallback((member: typeof roster[0]): boolean => {
    if (!member.hasData || !member.coverage) return false;
    return Object.values(member.coverage).some((val) => val === 'strong' || val === 'partial');
  }, []);

  const isRelevantForMember = useCallback(
    (member: typeof roster[0]): boolean => {
      if (!member.hasData) return false;
      if (selectedTopic === 'all') {
        return hasAnyOpinion(member);
      }
      const status = member.coverage[selectedTopic] || 'none';
      return status === 'strong' || status === 'partial';
    },
    [selectedTopic, hasAnyOpinion]
  );

  // Filtered members list
  const visibleMembers = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase('he');

    return roster.filter((member) => {
      const partyOk = selectedParty === 'all' || member.party === selectedParty;
      const searchable = `${member.name} ${member.party}`.toLocaleLowerCase('he');
      const queryOk = !query || searchable.includes(query);
      const relevanceOk = !relevantOnly || isRelevantForMember(member);
      return partyOk && queryOk && relevanceOk;
    });
  }, [roster, searchQuery, selectedParty, relevantOnly, isRelevantForMember]);

  // Grouped and sorted parties list
  const partyGroups = useMemo(() => {
    const grouped = new Map<string, typeof roster>();

    visibleMembers.forEach((member) => {
      const groupName = member.category === 'non_mk' ? NON_MK_CATEGORY : member.party;
      if (!grouped.has(groupName)) {
        grouped.set(groupName, []);
      }
      grouped.get(groupName)!.push(member);
    });

    const groups: PartyGroup[] = [...grouped.entries()].map(([name, partyMembers]) => ({
      name,
      members: partyMembers,
      seats: partyInfo[name]?.seats ?? 0,
      status: partyInfo[name]?.status ?? 'לשעבר',
      current: partyMembers.some((m) => m.current),
    }));

    groups.sort((a, b) => {
      if (sortMode === 'alpha') {
        return a.name.localeCompare(b.name, 'he');
      }
      if (a.current !== b.current) {
        return a.current ? -1 : 1;
      }
      return b.seats - a.seats || a.name.localeCompare(b.name, 'he');
    });

    return groups;
  }, [visibleMembers, partyInfo, sortMode]);

  const totalVisibleCount = visibleMembers.length;
  const relevantCount = useMemo(
    () => visibleMembers.filter(isRelevantForMember).length,
    [visibleMembers, isRelevantForMember]
  );

  const selectedTopicTitle =
    selectedTopic === 'all' ? 'כל הנושאים' : topicMap.get(selectedTopic)?.title || '';

  const selectedTopicDetails = selectedTopic === 'all' ? null : topicMap.get(selectedTopic);

  const activeMember = useMemo(() => {
    if (!selectedMemberKey) return null;
    return roster.find((m) => m.key === selectedMemberKey) || null;
  }, [selectedMemberKey, roster]);

  const activeSourcePost = useMemo((): TweetPost | null => {
    if (!selectedSourceKey) return null;
    return postMap.get(selectedSourceKey) || null;
  }, [selectedSourceKey, postMap]);

  // Walkthrough Tour Steps Configuration with State Driver Hooks
  const tourSteps = useMemo((): WalkthroughStepConfig[] => {
    const sampleMember = roster.find((m) => m.hasData) || roster[0];

    return [
      {
        id: 'header-controls',
        targetSelector: '[data-tour="controls-header"]',
        title: 'פילטרים וחיפוש',
        description:
          'חיפוש חברי כנסת לפי שם, סינון לפי מפלגה או נושא, ומעבר בין מצבי תצוגה.',
        preferredPlacement: 'bottom',
      },
      {
        id: 'status-summary',
        targetSelector: '[data-tour="status-summary"]',
        title: 'שורת סיכום',
        description:
          'מציגה את כמות חברי הכנסת המוצגים והמודגשים בהתאם לנושא הנבחר.',
        preferredPlacement: 'bottom',
      },
      {
        id: 'party-board',
        targetSelector: '[data-tour="party-board"]',
        title: 'כרטיסי חברי הכנסת',
        description:
          'תצוגה לפי מפלגות. צבע התגית על הכרטיס מסמן את מידת הפעילות או העמדה.',
        preferredPlacement: 'top',
      },
      {
        id: 'profile-drawer',
        targetSelector: '[data-tour="profile-drawer"]',
        title: 'פרופיל מפורט',
        description:
          'לחיצה על חבר כנסת פותחת את פירוט עמדותיו בכל הנושאים, לצד ציטוטים ומקורות.',
        preferredPlacement: 'start',
        onBeforeStep: async () => {
          if (sampleMember) {
            navigate(`/mk/${sampleMember.key}`);
            await loadEvidence(sampleMember);
          }
        },
        onAfterStep: () => {
          navigate('/');
        },
      },
    ];
  }, [roster, loadEvidence]);

  if (loading) {
    return (
      <>
        <SiteHeader />
        <main className="page" role="status" aria-label="טוען נתונים">
          <div className="skeleton-box skeleton-controls-bar" />
          <div className="skeleton-box skeleton-status-row" />
          <div className="skeleton-party-grid">
            {[1, 2, 3, 4, 5, 6].map((idx) => (
              <div key={idx} className="skeleton-party-island">
                <div className="skeleton-box" style={{ height: '24px', width: '60%' }} />
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
                  {[1, 2, 3, 4, 5, 6].map((mIdx) => (
                    <div key={mIdx} className="skeleton-member-node">
                      <div className="skeleton-box skeleton-member-avatar" />
                      <div className="skeleton-box skeleton-member-text" />
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </main>
        <SiteFooter />
      </>
    );
  }

  if (error) {
    return (
      <>
        <SiteHeader />
        <main className="page">
          <div className="empty-state">
            טעינת נתוני האתר נכשלה: {error}. יש להפעיל את האתר דרך <code>uv run mkwork</code> ולנסות שוב.
          </div>
        </main>
        <SiteFooter />
      </>
    );
  }

  return (
    <>
      <SiteHeader />
      <main className="page">
        <ControlsHeader
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        selectedParty={selectedParty}
        onPartyChange={setSelectedParty}
        sortMode={sortMode}
        onSortModeChange={setSortMode}
        relevantOnly={relevantOnly}
        onRelevantOnlyChange={setRelevantOnly}
        selectedTopic={selectedTopic}
        onTopicSelect={setSelectedTopic}
        topics={topics}
        parties={partiesList}
        theme={theme}
        onToggleTheme={toggleTheme}
        onOpenWelcome={resetWelcome}
      />

      <StatusSummaryRow
        totalVisible={totalVisibleCount}
        relevantCount={relevantCount}
        selectedTopicTitle={selectedTopicTitle}
      />

      {selectedTopicDetails && (
        <PartyScoreAxis
          members={roster}
          topic={selectedTopicDetails}
          selectedParty={selectedParty}
          onPartySelect={setSelectedParty}
          onSelectMember={(name) => {
            const member = roster.find((m) => m.name === name);
            if (member) {
              navigate(`/mk/${member.key}`);
            }
          }}
          imageFor={imageFor}
        />
      )}

      <PartyBoard
        parties={partyGroups}
        partyColors={partyColors}
        selectedTopic={selectedTopic}
        imageFor={imageFor}
        onSelectMember={(name) => {
          const member = roster.find((m) => m.name === name);
          if (member) {
            navigate(`/mk/${member.key}`);
          }
        }}
        onPrefetchMember={loadEvidence}
        isEmpty={visibleMembers.length === 0}
      />

      <Routes>
        <Route path="/mk/:id" element={
          <ProfileDrawer
            member={activeMember}
            selectedTopic={selectedTopic}
            topicMap={topicMap}
            topics={topics}
            isOpen={Boolean(activeMember)}
            onClose={() => navigate('/')}
            loadEvidence={loadEvidence}
            getTopicDetail={getTopicDetail}
            postMap={postMap}
            onOpenSource={setSelectedSourceKey}
            imageFor={imageFor}
            resolveHighResImage={resolveHighResImage}
            isSourceDialogOpen={Boolean(activeSourcePost)}
            hasEvidence={Boolean(activeMember && analysisMap.has(activeMember.key))}
          />
        } />
      </Routes>

      <SourceDialog
        post={activeSourcePost}
        onClose={() => {
          setSelectedSourceKey(null);
          if (isTourActive) {
            setIsTourActive(false);
          }
        }}
        isTourActive={isTourActive}
      />

      <WelcomeModal
        isOpen={isWelcomeOpen}
        onClose={dismissWelcome}
        onStartTour={() => {
          dismissWelcome();
          setIsTourActive(true);
        }}
        onSelectTopic={setSelectedTopic}
        topics={topics}
      />

      <WalkthroughTour
        steps={tourSteps}
        isActive={isTourActive}
        onComplete={() => setIsTourActive(false)}
        onSkip={() => setIsTourActive(false)}
      />
      </main>
      <SiteFooter />
    </>
  );
};
