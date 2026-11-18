/** Public account data returned by the API. */
export interface User {
  id: string;
  name: string;
  email: string;
  role: string;
  portfolioAccess: string[];
  status: string;
  defaultLandingPage: 'dashboard' | 'applications';
  mustChangePassword: boolean;
}

/** Signed session and its profile. */
export interface Session { token: string; user: User }
