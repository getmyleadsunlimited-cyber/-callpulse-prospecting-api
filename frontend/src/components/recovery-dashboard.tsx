"use client";

import { useEffect, useState } from "react";
import { ErrorState, LoadingState, EmptyState } from "@/components/states";
import styles from "./recovery-dashboard.module.css";

interface DashboardStats {
  prospectsCount: number;
  activeCampaigns: number;
  recoveredLeads: "not-available" | number;
  deliveriesCount: number;
  prospectDetails: Array<{
    id: number;
    email: string;
    company_name: string;
    campaignCount: number;
    deliveryCount: number;
  }>;
}

export function RecoveryDashboard({ initialData }: { initialData: DashboardStats | null }) {
  const [data, setData] = useState<DashboardStats | null>(initialData);
  const [loading, setLoading] = useState(!initialData);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initialData) return;

    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);
        const response = await fetch("/api/dashboard-stats", { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`Failed to fetch dashboard data: ${response.status}`);
        }
        const stats = (await response.json()) as DashboardStats;
        setData(stats);
      } catch (err) {
        setError(err instanceof Error ? err.message : "An unexpected error occurred");
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [initialData]);

  if (loading) {
    return <LoadingState label="Loading dashboard" />;
  }

  if (error) {
    return <ErrorState title="Failed to load dashboard" detail={error} />;
  }

  if (!data) {
    return <ErrorState title="No data available" detail="Dashboard data could not be loaded" />;
  }

  return (
    <div className={styles.container}>
      {/* Header */}
      <section className={styles.header}>
        <h1 className={styles.title}>AI Website Lead Recovery</h1>
        <p className={styles.subtitle}>Recover more value from the traffic you're already paying for.</p>
      </section>

      {/* Summary Cards */}
      <section className={styles.cardsGrid}>
        <div className={styles.card}>
          <div className={styles.cardLabel}>Prospects</div>
          <div className={styles.cardValue}>{data.prospectsCount}</div>
        </div>
        <div className={styles.card}>
          <div className={styles.cardLabel}>Active Campaigns</div>
          <div className={styles.cardValue}>{data.activeCampaigns}</div>
        </div>
        <div className={styles.card}>
          <div className={styles.cardLabel}>Recovered Leads</div>
          <div className={styles.cardValue}>
            {data.recoveredLeads === "not-available" ? (
              <span className={styles.unavailable}>Not available yet</span>
            ) : (
              data.recoveredLeads
            )}
          </div>
        </div>
        <div className={styles.card}>
          <div className={styles.cardLabel}>Deliveries</div>
          <div className={styles.cardValue}>{data.deliveriesCount}</div>
        </div>
      </section>

      {/* Recovered Leads Section */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Recovered Leads</h2>
        <div className={styles.emptyContent}>
          <p>Recovered lead data will appear here when the recovery feed is connected.</p>
        </div>
      </section>

      {/* Campaign Activity Section */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Campaign Activity</h2>
        {data.prospectDetails.length === 0 ? (
          <div className={styles.emptyContent}>
            <p>No campaign activity yet. Create a prospect and launch a campaign to get started.</p>
          </div>
        ) : (
          <div className={styles.activityTable}>
            <table>
              <thead>
                <tr>
                  <th>Prospect</th>
                  <th>Email</th>
                  <th>Campaigns</th>
                  <th>Deliveries</th>
                </tr>
              </thead>
              <tbody>
                {data.prospectDetails.map((prospect) => (
                  <tr key={prospect.id}>
                    <td>{prospect.company_name}</td>
                    <td className={styles.email}>{prospect.email}</td>
                    <td>{prospect.campaignCount}</td>
                    <td>{prospect.deliveryCount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
