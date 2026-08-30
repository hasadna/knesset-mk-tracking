import { TopicStatus } from '../types';

export function formatIsraeliDate(dateStr: string): string {
  if (!dateStr) return '';
  const trimmed = dateStr.trim();

  // If already in DD/MM/YYYY format
  if (/^\d{2}\/\d{2}\/\d{4}$/.test(trimmed)) {
    return trimmed;
  }

  // Handle ISO YYYY-MM-DD or YYYY-MM-DDTHH:mm:ss
  const isoMatch = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (isoMatch) {
    const [, year, month, day] = isoMatch;
    return `${day}/${month}/${year}`;
  }

  // Fallback JavaScript Date parsing
  const d = new Date(trimmed);
  if (!isNaN(d.getTime())) {
    const day = String(d.getDate()).padStart(2, '0');
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const year = d.getFullYear();
    return `${day}/${month}/${year}`;
  }

  return dateStr;
}

export function initials(name: string): string {
  if (!name) return '';
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '';
  if (parts.length === 1) return parts[0][0];
  return parts[0][0] + parts[parts.length - 1][0];
}

export function hexToRgba(hex: string, alpha: number): string {
  const normalized = hex.replace('#', '');
  const full =
    normalized.length === 3
      ? normalized
          .split('')
          .map((char) => char + char)
          .join('')
      : normalized;
  const number = parseInt(full, 16);
  const r = (number >> 16) & 255;
  const g = (number >> 8) & 255;
  const b = number & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export const STATUS_LABELS: Record<TopicStatus, string> = {
  strong: 'ודאות גבוהה',
  partial: 'ודאות בינונית',
  none: 'אין התייחסות',
};

export const STATUS_EXPLANATIONS: Record<TopicStatus, string> = {
  strong: 'עמדה מבוססת באופן חזק על סמך ציוצים וציטוטים ישירים במאגר',
  partial: 'כיסוי חלקי - מידע חלקי המציג כיוון עמדה אך ללא פירוט מלא',
  none: 'לא מספיק מידע - הנושא אינו מכוסה בציוצים שבמאגר',
};

// Rated per MK rather than positioned on a policy axis, so it is presented on its
// own throughout the UI instead of alongside the policy topics.
export const DISCOURSE_TOPIC_ID = 'discourse-quality';

export const PARTY_PALETTE = [
  '#2c5f8a', '#9a5d31', '#2f776d', '#7f5298', '#a14e64',
  '#547a2d', '#14779a', '#b36b20', '#5d6898', '#8d4141',
  '#466b64', '#8b6597', '#486282', '#707b2b'
];

export const TOPIC_COLORS: Record<string, string> = {
  'regional-security': '#1f6481',
  'gaza-day-after': '#d97706',
  'conflict-territories': '#b45309',
  'october-7': '#be123c',
  'haredi-draft': '#7c3aed',
  'judiciary': '#0f766e',
  'religion-state': '#4338ca',
  'jewish-arab': '#15803d',
  'police-crime': '#9f1239',
  'cost-budget': '#1e40af',
  'housing-transport': '#0e7490',
  'education-employment': '#6d28d9',
  'health-welfare': '#65a30d',
  'governance-trust': '#115e59',
  'media-rights': '#334155',
  'settler-violence': '#991b1b',
  'economic-reforms': '#1d4ed8',
  'haredi-conscription': '#6d28d9',
  'cost-of-living': '#15803d',
  'oct7-accountability': '#9f1239',
  'settlements-funding': '#b45309',
  'infrastructure': '#0284c7',
  'policing-crime': '#be123c',
  'economy-welfare': '#15803d',
  'security-peace': '#0369a1',
  'netanyahu-leadership': '#9f1239',
  'discourse-quality': '#7c3aed',
};

export function getTopicColor(topicId: string): string {
  return TOPIC_COLORS[topicId] || '#1f6481';
}

export function getPartyColor(partyName: string, index: number, totalPartiesMap: Map<string, string>): string {
  if (partyName === 'ישר! עם איזנקוט') return '#4c6d86';
  if (totalPartiesMap.has(partyName)) return totalPartiesMap.get(partyName)!;
  return PARTY_PALETTE[index % PARTY_PALETTE.length];
}

export function parseTweetText(rawText: string) {
  const rtMatch = rawText.match(/^RT\s+@([a-zA-Z0-9_]+):?\s*(.*)/is);
  if (rtMatch) {
    return {
      isRetweet: true,
      retweetAuthor: rtMatch[1],
      cleanText: rtMatch[2],
    };
  }
  return {
    isRetweet: false,
    retweetAuthor: null,
    cleanText: rawText,
  };
}
