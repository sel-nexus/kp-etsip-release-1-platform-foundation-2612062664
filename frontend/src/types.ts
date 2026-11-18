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

/** Portfolio fields and live aggregates from the API. */
export interface Portfolio {
  id: string; name: string; owner: string; description: string; productArea: string;
  status: 'Active' | 'Archived'; applicationCount: number; qualityScore: number | null;
  canEdit: boolean; canDelete: boolean;
}

/** Fields accepted when creating or updating a portfolio. */
export type PortfolioInput = Pick<Portfolio, 'name' | 'owner' | 'description' | 'productArea' | 'status'>;
export interface PortfolioPage { items: Portfolio[]; total: number; canCreate: boolean }
export interface ApplicationSummary { id: string; name: string; productArea: string; qualityScore: number | null; riskLevel: string }
export interface ApplicationPage { items: ApplicationSummary[]; total: number }

/** Editable application fields matching the inventory API. */
export interface ApplicationInput {
  name: string; description: string; portfolioId: string; productArea: string;
  businessOwner: string; technologyOwner: string; etsOwner: string;
  criticality: 'Low' | 'Medium' | 'High' | 'Critical'; tier: number;
  lifecycleStatus: 'Active' | 'Deprecated' | 'Retired'; technologyStack: string;
  repositoryLink: string | null; pipelineLink: string | null;
  applicationHealthScore: number; qualityScore: number; automationCoverage: number;
  unitTestCoverage: number; openDefects: number; openCriticalDefects: number;
  riskLevel: 'Low' | 'Medium' | 'High';
}
export interface Application extends ApplicationInput { id: string; portfolioName: string; canEdit: boolean; canDelete: boolean }
export interface ApplicationInventoryPage { items: Application[]; total: number; canCreate: boolean }

/** Signed session and its profile. */
export interface Session { token: string; user: User }
