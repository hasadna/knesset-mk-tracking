import React from 'react';
import { Topic, TopicStatus } from '../types';

interface RatingScaleProps {
  topic: Topic;
  rating?: number | null;
  quality: TopicStatus;
}

const parseAxisLabels = (description: string): [string, string] | null => {
  const axisLine = description.split(/\r?\n/, 1)[0]?.trim();
  const match = axisLine?.match(/^בין\s+(.+?)\s+ל(.+)$/);
  return match ? [match[1].trim(), match[2].trim()] : null;
};

export const RatingScale: React.FC<RatingScaleProps> = ({ topic, rating, quality }) => {
  const isRated =
    quality !== 'none' &&
    Number.isInteger(rating) &&
    Number(rating) >= 1 &&
    Number(rating) <= 5;

  if (!isRated) {
    return (
      <div className="rating-scale rating-scale-empty" aria-label="אין מספיק מידע לדירוג">
        <span className="rating-scale-kicker">דירוג עמדה</span>
        <strong>אין מספיק מידע לדירוג</strong>
      </div>
    );
  }

  const selectedRating = Number(rating);
  const scaleMeaning = topic.ratingScale?.[String(selectedRating)];
  const axisLabels = parseAxisLabels(topic.description);
  const firstPole = axisLabels?.[0] || topic.ratingScale?.['1'] || 'עמדה 1';
  const secondPole = axisLabels?.[1] || topic.ratingScale?.['5'] || 'עמדה 5';

  return (
    <figure
      className={`rating-scale rating-scale-${quality}`}
      aria-label={`דירוג ${selectedRating} מתוך 5. ${scaleMeaning || ''}`}
    >
      <figcaption className="rating-scale-heading">
        <span className="rating-scale-kicker">דירוג עמדה</span>
        <span className="rating-scale-value">{selectedRating} מתוך 5</span>
      </figcaption>

      <div className="rating-scale-poles" aria-hidden="true">
        <span>{firstPole}</span>
        <span>{secondPole}</span>
      </div>

      <div className="rating-scale-track" aria-label="משמעות ערכי סולם הדירוג">
        {[1, 2, 3, 4, 5].map((value) => {
          const meaning = topic.ratingScale?.[String(value)] || `דירוג ${value} מתוך 5`;
          const pos = value === 5 ? 'top-left' : value === 1 ? 'top-right' : 'top';
          return (
            <span
              key={value}
              className={`rating-scale-point ${value === selectedRating ? 'selected' : ''}`}
              data-tooltip={`דרגה ${value}: ${meaning}`}
              data-tooltip-pos={pos}
              tabIndex={0}
              aria-label={`${value}: ${meaning}`}
              aria-current={value === selectedRating ? 'true' : undefined}
            >
              {value}
            </span>
          );
        })}
      </div>

      {scaleMeaning && (
        <div className="rating-scale-meaning">
          <span className="ai-badge-chip">
            AI <i className="info-hint-icon" data-tooltip="פרשנות דירוג העמדה שנוצרה באופן אוטומטי באמצעות בינה מלאכותית" data-tooltip-pos="bottom">ⓘ</i>
          </span>
          {' '}
          {scaleMeaning}
        </div>
      )}
    </figure>
  );
};
