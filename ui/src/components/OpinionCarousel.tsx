import React from 'react';
import { Topic, TopicDetail, TweetPost } from '../types';
import { STATUS_EXPLANATIONS, STATUS_LABELS, formatIsraeliDate, parseTweetText } from '../utils/formatters';
import { RatingScale } from './RatingScale';
import { VoteChips } from './VoteChips';
import { ExternalLinkIcon, RepeatIcon } from './icons';
import { TopicPostCount } from './TopicPostCount';
import { IssueInfoButton } from './IssueInfoButton';


interface OpinionCarouselProps {
  topics: Topic[];
  currentIndex: number;
  onIndexChange?: (idx: number) => void;
  getTopicDetail: (topicId: string) => TopicDetail;
  postMap: Map<string, TweetPost>;
  onOpenSource: (sourceKey: string) => void;
  isLoading?: boolean;
}

export const OpinionCarousel: React.FC<OpinionCarouselProps> = ({
  topics,
  currentIndex,
  getTopicDetail,
  postMap,
  isLoading = false,
}) => {
  if (!topics.length) {
    return <div className="empty-profile">אין עמדות זמינות.</div>;
  }

  const currentTopic = topics[currentIndex];
  const details = getTopicDetail(currentTopic.id);

  const renderInlineTweetCards = (sourceKeys: string[]) => {
    if (!sourceKeys || !sourceKeys.length) return null;

    return (
      <div className="inline-tweets-list">
        {sourceKeys.map((key) => {
          const source = postMap.get(key);
          if (!source) return null;
          const formattedDate = formatIsraeliDate(source.date);
          const postUrl = source.url || (source.account && source.tweetId ? `https://x.com/${source.account}/status/${source.tweetId}` : '');
          const rawTweetText = source.text?.trim() || '(אין טקסט בציוץ זה)';
          const { isRetweet, retweetAuthor, cleanText } = parseTweetText(rawTweetText);

          return (
            <article key={key} className="inline-tweet-card">
              <header className="tweet-card-header">
                <span className="tweet-platform-date">טוויטר | {formattedDate}</span>
                {isRetweet && (
                  <span className="retweet-badge">
                    <RepeatIcon size={12} className="retweet-icon" />
                    ציוץ מחדש מ- @{retweetAuthor}
                  </span>
                )}
              </header>
              <div className="tweet-card-body">
                <p>{cleanText}</p>
              </div>
              {postUrl && (
                <footer className="tweet-card-footer">
                  <a href={postUrl} target="_blank" rel="noopener noreferrer" className="original-tweet-link">
                    לציוץ המקורי <ExternalLinkIcon size={13} />
                  </a>
                </footer>
              )}
            </article>
          );
        })}
      </div>
    );
  };

  return (
    <div id="interactiveTopicContainer">
      <div className="topics-interactive-container">
        <div className="vibrant-opinion-card">
          <div className="topic-card-header">
            <h3>
              {currentTopic.title}
            </h3>
            <div className="topic-signal-row">
              <span
                className={`status-badge ${details.status}`}
                data-tooltip={STATUS_EXPLANATIONS[details.status]}
              >
                {STATUS_LABELS[details.status]}
              </span>
              <TopicPostCount count={details.postCount} />
            </div>
          </div>

          {isLoading ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', width: '100%', alignItems: 'center', margin: '20px 0' }}>
              <div className="skeleton-box" style={{ height: '18px', width: '85%' }} />
              <div className="skeleton-box" style={{ height: '18px', width: '65%' }} />
            </div>
          ) : (
            <>
              <div className="topic-stance-body">
                <IssueInfoButton topic={currentTopic} modelVersion={details?.modelVersion}>
                  <span className="ai-badge-chip">
                    AI
                  </span>
                </IssueInfoButton>
                {' '}
                {details.stance}
              </div>

              <RatingScale
                topic={currentTopic}
                rating={details.rating}
                quality={details.status}
              />

              {details.extendedStance && (
                <div className="topic-extended-body">
                  <IssueInfoButton topic={currentTopic} modelVersion={details?.modelVersion}>
                    <span className="ai-badge-chip">
                      AI
                    </span>
                  </IssueInfoButton>
                  {' '}
                  {details.extendedStance}
                </div>
              )}

              <VoteChips votes={details.votes} summary={details.voteSummary} />

              {renderInlineTweetCards(details.sources)}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
