import React from 'react';
import './SiteFooter.css';

const GithubIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"></path>
  </svg>
);

const LandmarkIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <line x1="3" y1="22" x2="21" y2="22"></line>
    <line x1="6" y1="18" x2="6" y2="11"></line>
    <line x1="10" y1="18" x2="10" y2="11"></line>
    <line x1="14" y1="18" x2="14" y2="11"></line>
    <line x1="18" y1="18" x2="18" y2="11"></line>
    <polygon points="12 2 20 7 4 7"></polygon>
  </svg>
);



export const SiteFooter: React.FC = () => {
  return (
    <footer className="site-footer">
      <div className="site-footer-content">
        <a href="https://hasadna.org.il/" target="_blank" rel="noopener noreferrer" className="footer-link">
          <img src="https://github.com/hasadna.png" alt="הסדנא לידע ציבורי" width="20" height="20" style={{ objectFit: 'contain', borderRadius: '50%' }} />
          <span>הסדנא לידע ציבורי</span>
        </a>
        <a href="https://www.over.org.il/" target="_blank" rel="noopener noreferrer" className="footer-link">
          <LandmarkIcon />
          <span>Over - גרסאות לעם</span>
        </a>
        <a href="https://github.com/hasadna/knesset-mk-tracking" target="_blank" rel="noopener noreferrer" className="footer-link">
          <GithubIcon />
          <span>קוד פתוח ב-GitHub</span>
        </a>
      </div>
    </footer>
  );
};
