import React, { useEffect, useState, useMemo, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { MKMember, Topic, TopicDetail, TweetPost } from '../types';
import { initials, STATUS_LABELS, formatIsraeliDate, parseTweetText, DISCOURSE_TOPIC_ID } from '../utils/formatters';
import { OpinionCarousel } from './OpinionCarousel';
import { XIcon, ArrowRightIcon, RepeatIcon } from './icons';
import { RatingScale } from './RatingScale';
import { TopicPostCount, getTopicPillActivityClass } from './TopicPostCount';
import { IssueInfoButton } from './IssueInfoButton';
import { SourcesInfoButton } from './SourcesInfoButton';

interface ProfileDrawerProps {
  member: MKMember | null;
  selectedTopic: string;
  topicMap: Map<string, Topic>;
  topics: Topic[];
  isOpen: boolean;
  onClose: () => void;
  loadEvidence: (member: MKMember) => Promise<void>;
  getTopicDetail: (memberKey: string, topicId: string) => TopicDetail;
  postMap: Map<string, TweetPost>;
  onOpenSource: (sourceKey: string) => void;
  imageFor: (member: MKMember) => string;
  resolveHighResImage: (title: string) => string;
  isSourceDialogOpen: boolean;
  hasEvidence: boolean;
}

const ProfileSkeleton: React.FC = () => {
  return (
    <div className="profile-skeleton" role="status">
      <aside className="profile-aside">
        <div className="skeleton-box skeleton-avatar" />
        <div className="skeleton-box skeleton-title" />
        <div className="skeleton-box skeleton-subtitle" />
        <div className="skeleton-box" style={{ height: '120px', marginTop: '14px' }} />
      </aside>
      <section className="profile-main">
        <div className="skeleton-box skeleton-title" style={{ width: '40%', marginBottom: '20px' }} />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px' }}>
          <div className="skeleton-box skeleton-card" />
          <div className="skeleton-box skeleton-card" />
          <div className="skeleton-box skeleton-card" />
          <div className="skeleton-box skeleton-card" />
        </div>
      </section>
    </div>
  );
};

export const ProfileDrawer: React.FC<ProfileDrawerProps> = ({
  member,
  selectedTopic,
  topicMap,
  topics,
  isOpen,
  onClose,
  loadEvidence,
  getTopicDetail,
  postMap,
  onOpenSource,
  imageFor,
  resolveHighResImage,
  isSourceDialogOpen,
  hasEvidence,
}) => {
  const [evidenceLoading, setEvidenceLoading] = useState<boolean>(false);
  const [evidenceError, setEvidenceError] = useState<boolean>(false);
  const [drawerMode, setDrawerMode] = useState<'overview' | 'detail'>('overview');
  const [currentTopicIndex, setCurrentTopicIndex] = useState<number>(0);
  const [displayImageSrc, setDisplayImageSrc] = useState<string>('');
  const [isMobile, setIsMobile] = useState<boolean>(false);
  const cardRefs = useRef<(HTMLDivElement | null)[]>([]);

  // Mobile detection
  useEffect(() => {
    const checkMobile = () => setIsMobile(window.innerWidth <= 650);
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // Lock body scroll when drawer is open
  useEffect(() => {
    if (isOpen) {
      document.body.classList.add('drawer-open');
    } else {
      document.body.classList.remove('drawer-open');
    }
    return () => {
      document.body.classList.remove('drawer-open');
    };
  }, [isOpen]);

  // Load evidence separately from images so an arriving Wikipedia image batch
  // cannot restart the drawer's loading state.
  useEffect(() => {
    if (!isOpen || !member) return;

    setEvidenceLoading(!hasEvidence);
    setEvidenceError(false);
    setDrawerMode('overview');
    setCurrentTopicIndex(0);

    if (hasEvidence) return;

    loadEvidence(member)
      .then(() => setEvidenceLoading(false))
      .catch((err) => {
        console.error('Failed to load evidence', err);
        setEvidenceError(true);
        setEvidenceLoading(false);
      });
  }, [isOpen, member?.key, loadEvidence, hasEvidence]);

  useEffect(() => {
    if (!isOpen || !member) return;

    setDisplayImageSrc(imageFor(member));
    if (!member.wikiTitle) return;

    const highRes = resolveHighResImage(member.wikiTitle);
    if (!highRes) return;

    let isMounted = true;
    const img = new Image();
    img.src = highRes;
    img.onload = () => {
      if (isMounted) setDisplayImageSrc(highRes);
    };

    return () => {
      isMounted = false;
      img.onload = null;
      img.onerror = null;
    };
  }, [isOpen, member?.key, member?.wikiTitle, imageFor, resolveHighResImage]);

  const discourseTopic = useMemo(
    () => topics.find((topic) => topic.id === DISCOURSE_TOPIC_ID) || null,
    [topics]
  );
  const policyTopics = useMemo(() => {
    const filtered = topics.filter((topic) => topic.id !== DISCOURSE_TOPIC_ID);
    if (!member) return filtered;

    return [...filtered].sort((a, b) => {
      const detailA = getTopicDetail(member.key, a.id);
      const detailB = getTopicDetail(member.key, b.id);
      const countA = detailA.postCount || detailA.sources?.length || 0;
      const countB = detailB.postCount || detailB.sources?.length || 0;
      return countB - countA;
    });
  }, [topics, member, getTopicDetail]);
  const discourseDetails =
    member && discourseTopic ? getTopicDetail(member.key, discourseTopic.id) : null;

  // Keyboard navigation listener for ArrowLeft, ArrowRight, Esc
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (isSourceDialogOpen) return;

      if (event.key === 'Escape') {
        if (drawerMode === 'detail') {
          setDrawerMode('overview');
          setTimeout(() => {
            const card = cardRefs.current[currentTopicIndex];
            if (card) {
              card.focus();
            }
          }, 0);
        } else {
          onClose();
        }
      } else if (drawerMode === 'detail') {
        if (event.key === 'ArrowLeft') {
          // Next in RTL is ArrowLeft
          setCurrentTopicIndex((prev) =>
            prev < policyTopics.length - 1 ? prev + 1 : prev
          );
        } else if (event.key === 'ArrowRight') {
          // Prev in RTL is ArrowRight
          setCurrentTopicIndex((prev) => (prev > 0 ? prev - 1 : prev));
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, isSourceDialogOpen, drawerMode, onClose, policyTopics.length]);

  const selectedTopicObj = selectedTopic !== 'all' ? topicMap.get(selectedTopic) : null;
  const selectedDetails = member && selectedTopicObj && member.hasData
    ? getTopicDetail(member.key, selectedTopicObj.id)
    : null;

  const drawerContextText = evidenceLoading
    ? 'טוען נתוני עמדות וציוצים…'
    : evidenceError
    ? 'טעינת נתוני הפרופיל נכשלה'
    : selectedTopicObj
    ? `הפרופיל מוצג בהקשר של: ${selectedTopicObj.title}`
    : '';

  const renderSourceButtons = (sourceKeys: string[], isCollapsible = true) => {
    if (!sourceKeys || !sourceKeys.length) return null;

    const tweetChips = (
      <div className="sources">
        {sourceKeys.map((key) => {
          const source = postMap.get(key);
          if (!source) return null;
          const text = source.text || '';
          const { isRetweet, cleanText } = parseTweetText(text);
          const snippet = cleanText.length > 40 ? cleanText.slice(0, 40) + '…' : cleanText;
          return (
            <button
              key={key}
              className="tweet-chip"
              type="button"
              data-source-key={key}
              onClick={() => onOpenSource(key)}
            >
              <span className="tweet-chip-snippet">"{snippet}"</span>
              <span className="tweet-chip-date">
                {isRetweet && <RepeatIcon size={10} className="retweet-icon" />} ציוץ {isRetweet ? 'מחדש ' : ''}{formatIsraeliDate(source.date)}
              </span>
            </button>
          );
        })}
      </div>
    );

    if (isCollapsible) {
      return (
        <details className="sources-collapsible">
          <summary className="sources-summary-btn">
            <span>מקורות וציוצים תומכים ({sourceKeys.length})</span>
            <SourcesInfoButton />
          </summary>
          <div className="sources-container">
            {tweetChips}
          </div>
        </details>
      );
    }

    return (
      <div className="sources-container">
        <div className="sources-label-row">
          <span className="sources-label">מקורות וציוצים תומכים</span>
          <SourcesInfoButton />
        </div>
        {tweetChips}
      </div>
    );
  };

  const renderDiscourseWidget = () => {
    if (!discourseTopic || !discourseDetails) return null;

    const rating = discourseDetails.rating;
    const isRated =
      discourseDetails.status !== 'none' &&
      Number.isInteger(rating) &&
      Number(rating) >= 1 &&
      Number(rating) <= 5;

    return (
      <section className={`discourse-widget ${discourseDetails.status}`}>
        <div className="discourse-widget-copy">
          <div className="discourse-widget-header-row">
            <span className="discourse-widget-eyebrow">אופן ההתבטאות</span>
            <div className="discourse-title-row">
              <h2>{discourseTopic.title}</h2>
              <span className={`status-badge ${discourseDetails.status}`}>
                {STATUS_LABELS[discourseDetails.status]}
              </span>
              {discourseDetails.status === 'none' || (!discourseDetails.postCount && (!discourseDetails.sources || discourseDetails.sources.length === 0)) ? (
                <span className="topic-count-pill empty">אין התייחסות</span>
              ) : (
                <span className={`topic-count-pill ${getTopicPillActivityClass(discourseDetails.postCount || discourseDetails.sources?.length || 0)}`}>
                  {discourseDetails.postCount || discourseDetails.sources?.length || 0} ציוצים בנושא
                </span>
              )}
            </div>
          </div>

          <div
            className="discourse-meter"
            aria-label={
              isRated
                ? `איכות השיח: דירוג ${rating} מתוך 5`
                : 'אין מספיק מידע לדירוג איכות השיח'
            }
          >
            <div className="discourse-meter-labels" aria-hidden="true">
              <span>מכבד וענייני</span>
              <span>פוגעני ואישי</span>
            </div>
            <div className="discourse-meter-points" aria-label="משמעות ערכי איכות השיח">
              {[1, 2, 3, 4, 5].map((value) => {
                const meaning =
                  discourseTopic.ratingScale?.[String(value)] || `דירוג ${value} מתוך 5`;
                const pos = value === 5 ? 'top-left' : value === 1 ? 'top-right' : 'top';
                return (
                  <i
                    key={value}
                    className={isRated && value === Number(rating) ? 'selected' : ''}
                    data-tooltip={meaning}
                    data-tooltip-pos={pos}
                    tabIndex={0}
                    aria-label={`${value}: ${meaning}`}
                  />
                );
              })}
            </div>
          </div>

          <p>
            <IssueInfoButton topic={discourseTopic} modelVersion={discourseDetails?.modelVersion}>
              <span className="ai-badge-chip">
                AI
              </span>
            </IssueInfoButton>
            {' '}
            {discourseDetails.status === 'none'
              ? 'אין מספיק מידע להערכת איכות השיח.'
              : discourseDetails.stance}
          </p>
          {discourseDetails.extendedStance && (
            <details className="extended-stance">
              <summary>מידע נוסף</summary>
              <p>{discourseDetails.extendedStance}</p>
            </details>
          )}
          {renderSourceButtons(discourseDetails.sources, true)}
        </div>
      </section>
    );
  };

  const renderTopicFocus = () => {
    if (!selectedTopicObj || !member) return null;

    if (!member.hasData || !selectedDetails || selectedDetails.status === 'none') {
      return (
        <div className="topic-focus">
          <div className="topic-focus-label topic-signal-row">
            <span>הנושא המסונן</span>
            <TopicPostCount count={selectedDetails?.postCount || 0} />
          </div>
          <h3>
            {selectedTopicObj.title}
            <IssueInfoButton topic={selectedTopicObj} />
          </h3>
          <p>לא נמצאה התייחסות רלוונטית של {member.name} לנושא זה במאגר.</p>
        </div>
      );
    }

    return (
      <div className="topic-focus">
        <div className="topic-focus-label topic-signal-row">
          <span>הנושא המסונן · {STATUS_LABELS[selectedDetails.status]}</span>
          <TopicPostCount count={selectedDetails.postCount} />
        </div>
        <h3>{selectedTopicObj.title}</h3>
        <p>
          <IssueInfoButton topic={selectedTopicObj} modelVersion={selectedDetails?.modelVersion}>
            <span className="ai-badge-chip">
              AI
            </span>
          </IssueInfoButton>
          {' '}
          {selectedDetails.stance}
        </p>
        <RatingScale
          topic={selectedTopicObj}
          rating={selectedDetails.rating}
          quality={selectedDetails.status}
        />
        {selectedDetails.extendedStance && (
          <details className="extended-stance">
            <summary>מידע נוסף</summary>
            <p>{selectedDetails.extendedStance}</p>
          </details>
        )}
        {renderSourceButtons(selectedDetails.sources)}
      </div>
    );
  };

  const handleSelectTopicFromOverview = (index: number) => {
    setCurrentTopicIndex(index);
    setDrawerMode('detail');
  };

  const drawerContent = (
    <div className="drawer-body">
          <div className="drawer-toolbar">
            {drawerMode === 'detail' && member ? (
              <div style={{ display: 'flex', justifyContent: 'flex-start', width: '100%' }}>
                <button
                  className="close-drawer-btn"
                  type="button"
                  onClick={() => {
                    setDrawerMode('overview');
                    setTimeout(() => {
                      const card = cardRefs.current[currentTopicIndex];
                      if (card) {
                        card.focus();
                      }
                    }, 0);
                  }}
                >
                  <ArrowRightIcon size={14} /> חזרה לסקירת הנושאים
                </button>
              </div>
            ) : (
              <span id="drawerContext" className="drawer-context">
                {drawerContextText}
              </span>
            )}
          </div>

        <div id="profileContent">
          {member ? (
            evidenceLoading ? (
              <ProfileSkeleton />
            ) : evidenceError ? (
              <div className="empty-profile" role="alert">
                לא ניתן לטעון כרגע את נתוני העמדות והציוצים. יש לנסות שוב מאוחר יותר.
              </div>
            ) : (
              <div className="profile">
                <aside className="profile-aside">
                  <div className={`profile-image ${displayImageSrc ? 'has-photo loaded' : 'loaded'}`}>
                    <div className="fallback">{initials(member.name)}</div>
                    {displayImageSrc && (
                      <img
                        className="loaded"
                        src={displayImageSrc}
                        alt={`תמונת דיוקן של ${member.name}`}
                        onError={() => setDisplayImageSrc('')}
                      />
                    )}
                  </div>

                  <div>
                    <h2 className="profile-name">{member.name}</h2>
                    <div className="profile-party">{member.party}</div>
                  </div>

                  <div className="party-box">
                    <div className="party-row">
                      <span>מעמד</span>
                      <strong>
                        {member.current ? 'חבר/ת כנסת מכהן/ת' : 'חבר כנסת לשעבר'}
                      </strong>
                    </div>
                    <div className="party-row">
                      <span>מסגרת פוליטית</span>
                      <strong>{member.bloc}</strong>
                    </div>
                    <div className="party-row">
                      <span>מושבים בכנסת</span>
                      <strong>{member.current ? member.seats : '—'}</strong>
                    </div>
                    <div className="party-row">
                      <span>ציוצים במאגר</span>
                      <strong>{member.postCount}</strong>
                    </div>
                  </div>
                </aside>

                <section className="profile-main">
                  {selectedTopicObj && renderTopicFocus()}

                  {member.hasData ? (
                    drawerMode === 'overview' ? (
                      <div>
                        {renderDiscourseWidget()}
                        <div className="topic-overview-list" data-tour="stance-breakdown">
                          {policyTopics.map((t, idx) => {
                            const detail = getTopicDetail(member.key, t.id);
                            const topicStatus = (member.coverage?.[t.id] as 'strong' | 'partial' | 'none') || detail.status;
                            const isNoEvidence = topicStatus === 'none' || detail.postCount === 0;

                            return (
                              <div
                                key={t.id}
                                ref={(el) => { cardRefs.current[idx] = el; }}
                                className={`topic-list-row-card ${isNoEvidence ? 'no-evidence' : ''}`}
                                role="button"
                                tabIndex={0}
                                aria-label={`הצגת עמדה מורחבת בנושא ${t.title}`}
                                onClick={() => handleSelectTopicFromOverview(idx)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter' || e.key === ' ') {
                                    handleSelectTopicFromOverview(idx);
                                  }
                                }}
                              >
                                <div className="topic-card-right">
                                  <h3>{t.title}</h3>
                                  <div className="topic-stance-snippet">
                                    {detail.stance ? (
                                      <>
                                        <IssueInfoButton topic={t} modelVersion={detail?.modelVersion}>
                                          <span className="ai-badge-chip">
                                            AI
                                          </span>
                                        </IssueInfoButton>
                                        {' '}
                                        {detail.stance}
                                      </>
                                    ) : (
                                      'אין פירוט זמין בנושא זה.'
                                    )}
                                  </div>
                                </div>
                                <div className="topic-card-left" style={{ display: 'flex', flexDirection: 'column', gap: '6px', alignItems: 'flex-end' }}>
                                  {isNoEvidence ? (
                                    <span className="topic-count-pill empty">אין התייחסות</span>
                                  ) : (
                                    <span className={`topic-count-pill ${getTopicPillActivityClass(detail.postCount || detail.sources?.length || 0)}`}>
                                      {detail.postCount || detail.sources?.length || 0} ציוצים בנושא
                                    </span>
                                  )}
                                  {detail.votes && detail.votes.length > 0 && (
                                    <span className="topic-count-pill vote-count-badge">
                                      🗳️ {detail.votes.length} הצבעות
                                    </span>
                                  )}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ) : (
                      <div>
                        <div className="topic-card-nav-bar" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px', width: '100%' }}>
                          <button
                            id="prevTopicBtn"
                            className="topic-nav-btn"
                            type="button"
                            disabled={currentTopicIndex === 0}
                            aria-label="העמדה הקודמת"
                            onClick={() => currentTopicIndex > 0 && setCurrentTopicIndex(currentTopicIndex - 1)}
                          >
                            ► הקודם
                          </button>

                          <div className="topic-nav-dots" role="navigation" aria-label="בחירת עמדה" style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                            {policyTopics.map((t, idx) => {
                              const status = getTopicDetail(member.key, t.id).status;
                              const isActive = idx === currentTopicIndex;
                              return (
                                <button
                                  key={t.id}
                                  className={`dot ${isActive ? 'active' : ''} ${status}`}
                                  type="button"
                                  data-card-idx={idx}
                                  aria-label={`עמדה ${idx + 1} - ${t.title}`}
                                  data-tooltip={`${t.title} (${STATUS_LABELS[status]})`}
                                  onClick={() => setCurrentTopicIndex(idx)}
                                />
                              );
                            })}
                          </div>

                          <button
                            id="nextTopicBtn"
                            className="topic-nav-btn"
                            type="button"
                            disabled={currentTopicIndex === policyTopics.length - 1}
                            aria-label="העמדה הבאה"
                            onClick={() => currentTopicIndex < policyTopics.length - 1 && setCurrentTopicIndex(currentTopicIndex + 1)}
                          >
                            הבא ◄
                          </button>
                        </div>

                        <OpinionCarousel
                          topics={policyTopics}
                          currentIndex={currentTopicIndex}
                          onIndexChange={setCurrentTopicIndex}
                          getTopicDetail={(tId) => getTopicDetail(member.key, tId)}
                          postMap={postMap}
                          onOpenSource={onOpenSource}
                          isLoading={evidenceLoading || !getTopicDetail(member.key, policyTopics[currentTopicIndex]?.id || '').stance}
                        />
                      </div>
                    )
                  ) : (
                    <div className="empty-profile">
                      אין ציוצים זמינים עבור {member.name} במאגר. הפרופיל מציג מידע מפלגתי בסיסי בלבד.
                    </div>
                  )}
                </section>
              </div>
            )
          ) : null}
        </div>
      </div>
  );

  return (
    <>
      {isMobile ? (
        <AnimatePresence>
          {isOpen && (
            <motion.div 
              className="mobile-fullscreen-page"
              initial={{ x: '100%' }}
              animate={{ x: 0 }}
              exit={{ x: '100%' }}
              transition={{ type: 'spring', damping: 25, stiffness: 200 }}
            >
              <div className="mobile-header">
                <button className="mobile-back-btn" onClick={onClose}>
                  <ArrowRightIcon size={20} />
                </button>
                <span className="mobile-header-title">{member?.name}</span>
              </div>
              <div className="mobile-scroll-container">
                {drawerContent}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      ) : (
        <>
          <div
            id="drawerBackdrop"
            className={`drawer-backdrop ${isOpen ? 'open' : ''}`}
            onClick={onClose}
          />
          <aside
            id="profileDrawer"
            className={`drawer ${isOpen ? 'open' : ''}`}
            data-tour="profile-drawer"
            role="dialog"
            aria-modal="true"
            aria-hidden={!isOpen}
            aria-label="פרופיל חבר הכנסת"
          >
            <button id="closeDrawer" className="floating-close" type="button" onClick={onClose} aria-label="סגירה">
              <XIcon size={16} />
            </button>
            {drawerContent}
          </aside>
        </>
      )}
    </>
  );
};
