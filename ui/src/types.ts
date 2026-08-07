export type TopicStatus = 'strong' | 'partial' | 'none';

export interface MKMember {
  key: string;
  name: string;
  wikiTitle: string;
  party: string;
  category: 'current_mk' | 'non_mk';
  seats: number;
  bloc: string;
  current: boolean;
  hasData: boolean;
  postCount: number;
  coverage: Record<string, TopicStatus>;
  imageUrl?: string;
  account?: string;
  bio?: string;
}

export interface Topic {
  id: string;
  title: string;
  description: string;
  promptForSocialPostSimilarity?: string;
  group?: string;
  tags?: string[];
  contrast?: string;
  ratingScale?: Record<string, string>;
}

export type VoteDecision = 'for' | 'against' | 'abstain' | 'absent';

export interface VoteItem {
  id: string;
  title: string;
  vote: VoteDecision;
  date: string;
  eventKind?: 'plenum' | 'committee';
  documentUri?: string | null;
  summary?: string | null;
}

export interface VoteSummary {
  forCount: number;
  againstCount: number;
  abstainCount: number;
  absentCount: number;
}

export interface TopicDetail {
  id: string;
  status: TopicStatus;
  rating?: number | null;
  postCount: number;
  stance: string;
  extendedStance?: string;
  sources: string[];
  limitations?: string;
  modelVersion?: string | null;
  votes?: VoteItem[];
  voteSummary?: VoteSummary;
}

export interface TweetPost {
  key: string;
  sourceType?: string;
  publisher: string;
  account: string;
  date: string;
  createdAt?: string;
  text: string;
  url?: string;
  tweetId?: string;
  metrics?: Record<string, unknown>;
  referencedTweets?: unknown[];
  label?: string;
  isSummarySource?: boolean;
}

export interface MKAnalysis {
  mkKey: string;
  topics: TopicDetail[];
  sourcePosts: TweetPost[];
}

export type SortMode = 'seats' | 'alpha';
export type Theme = 'light' | 'dark';

export interface PartyGroup {
  name: string;
  members: MKMember[];
  seats: number;
  status: string;
  current: boolean;
}
