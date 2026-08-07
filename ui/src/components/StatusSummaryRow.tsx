import React from 'react';
import { CalendarIcon } from './icons';

interface StatusSummaryRowProps {
  totalVisible: number;
  relevantCount: number;
  selectedTopicTitle: string;
}

export const StatusSummaryRow: React.FC<StatusSummaryRowProps> = ({
  totalVisible,
  relevantCount,
  selectedTopicTitle,
}) => {
  return (
    <div className="status-row" data-tour="status-summary">
      <span id="resultText" aria-live="polite">
        {`${totalVisible} דמויות מוצגות · ${relevantCount} מודגשות · ${selectedTopicTitle}`}
      </span>
      <span className="analysis-date-badge">
        <CalendarIcon size={14} /> תאריך עדכון ניתוח: 01/07/2026
      </span>
      <span>מקור עמדות: מאגר הנתונים</span>
      <div className="legend">
        <span
          className="legend-item"
          data-tooltip="עמדה מבוססת באופן חזק על סמך ציוצים וציטוטים ישירים במאגר"
          tabIndex={0}
        >
          <i className="legend-swatch strong"></i>ודאות גבוהה
        </span>
        <span
          className="legend-item"
          data-tooltip="כיסוי חלקי - מידע חלקי המציג כיוון עמדה אך ללא פירוט מלא"
          tabIndex={0}
        >
          <i className="legend-swatch partial"></i>ודאות בינונית
        </span>
        <span
          className="legend-item"
          data-tooltip="לא מספיק מידע - הנושא אינו מכוסה בציוצים שבמאגר"
          tabIndex={0}
        >
          <i className="legend-swatch none"></i>אין התייחסות
        </span>
      </div>
    </div>
  );
};
