import React from 'react';

interface TopicPostCountProps {
  count: number;
}

export const getTopicPillActivityClass = (count: number): string => {
  if (!count || count <= 0) return 'empty';
  if (count >= 10) return 'activity-vibrant-high';
  if (count >= 5) return 'activity-vibrant-medium';
  return 'activity-vibrant-low';
};

export const TopicPostCount: React.FC<TopicPostCountProps> = ({ count }) => {
  const safeCount = Number.isFinite(count) ? Math.max(0, count) : 0;
  const label = safeCount === 1 ? 'ציוץ אחד בנושא' : `${safeCount} ציוצים בנושא`;
  const activityClass = getTopicPillActivityClass(safeCount);

  return (
    <span
      className={`topic-post-count topic-count-pill ${activityClass}`}
      data-tooltip="ציוצים עם ציון התאמה של 0.20 ומעלה, וכן ציוצים שמופיעים כמקור בסיכום. הכמות מעידה על היקף העיסוק, לא בהכרח על חוזק העמדה."
      data-tooltip-pos="bottom"
      tabIndex={0}
      aria-label={label}
    >
      <span className="topic-post-count-bars" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {label}
    </span>
  );
};
