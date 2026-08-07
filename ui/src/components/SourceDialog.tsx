import React, { useEffect, useRef } from 'react';
import { TweetPost } from '../types';
import { formatIsraeliDate, parseTweetText } from '../utils/formatters';
import { XIcon, ExternalLinkIcon, RepeatIcon } from './icons';

interface SourceDialogProps {
  post: TweetPost | null;
  onClose: () => void;
  isTourActive?: boolean;
}

export const SourceDialog: React.FC<SourceDialogProps> = ({ post, onClose, isTourActive = false }) => {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (post) {
      if (!dialog.open) {
        dialog.showModal();
      }
    } else {
      if (dialog.open) {
        dialog.close();
      }
    }
  }, [post]);

  const formattedDate = post ? formatIsraeliDate(post.date) : '';
  const parsedPost = post ? parseTweetText(post.text || '') : null;
  const isRetweet = parsedPost?.isRetweet;
  
  const sourceLabel = post ? (isRetweet ? `ציוץ מחדש ב-X · ${formattedDate}` : `ציוץ ב-X · ${formattedDate}`) : '';
  const rawUrl = post?.url || (post?.account && post?.tweetId ? `https://x.com/${post.account}/status/${post.tweetId}` : '');
  const postUrl = (() => {
    if (!rawUrl) return '';
    try {
      const parsed = new URL(rawUrl);
      return (parsed.protocol === 'http:' || parsed.protocol === 'https:') ? rawUrl : '';
    } catch {
      return '';
    }
  })();

  return (
    <dialog
      id="sourceDialog"
      data-tour="source-dialog"
      className={isTourActive ? 'tour-active-dialog' : ''}
      ref={dialogRef}
      aria-labelledby="sourceTitle"
      aria-modal="true"
      onClose={onClose}
    >
      <div className="source-head">
        <h2 id="sourceTitle">
          {post ? `מקור — ${sourceLabel}` : 'פוסט מקור'}
        </h2>
        <button id="closeSource" className="close" type="button" onClick={onClose} aria-label="סגירה">
          <XIcon size={16} />
        </button>
      </div>

      <div id="sourceBody" className="source-body">
        {post ? (
          <>
            <div className="source-meta">
              <span>{post.publisher}</span>
              <span>{formattedDate}</span>
              <span>{sourceLabel}</span>
            </div>
            {isRetweet && (
              <div className="retweet-badge" style={{ marginBottom: '8px' }}>
                <RepeatIcon size={14} className="retweet-icon" />
                <span>ציוץ מחדש מ- @{parsedPost?.retweetAuthor}</span>
              </div>
            )}
            <div className="source-text">{parsedPost?.cleanText}</div>
            {postUrl && (
              <div className="source-link">
                <a
                  href={postUrl}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(e) => e.stopPropagation()}
                >
                  פתיחת הציוץ המקורי <ExternalLinkIcon size={14} />
                </a>
              </div>
            )}
          </>
        ) : null}
      </div>
    </dialog>
  );
};
