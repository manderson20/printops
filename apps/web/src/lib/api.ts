export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type HealthStatus = {
  status: string;
  service: string;
};

export async function getHealth(): Promise<HealthStatus> {
  const response = await fetch(`${API_URL}/healthz`);
  if (!response.ok) {
    throw new Error(`API health check failed: ${response.status}`);
  }
  return response.json();
}
