(function (root) {
    'use strict';

    const SCENARIOS = {
        conservative: { established: 0.95, emerging: 0.65, campaign: 0.85 },
        expected: { established: 1.00, emerging: 0.85, campaign: 1.00 },
        upside: { established: 1.05, emerging: 1.00, campaign: 1.15 }
    };

    function categoryType(category) {
        if (category.includes('New 2026')) return 'emerging';
        if (category.includes('Multi-Year') ||
            category.includes('Recurring') ||
            category === 'Base Business (Override)') return 'established';
        return 'campaign';
    }

    function profileMonthlyTons(profile, year, excludedWmus) {
        const totals = Array(12).fill(0);
        Object.entries(profile.by_wmu).forEach(([wmu, data]) => {
            if (excludedWmus.has(wmu)) return;
            const values = data.tons_monthly[year] || [];
            for (let month = 0; month < 12; month++) totals[month] += values[month] || 0;
        });
        return totals;
    }

    function sum(values, end = values.length) {
        return values.slice(0, end).reduce((total, value) => total + value, 0);
    }

    function average(values) {
        return values.length ? sum(values) / values.length : 0;
    }

    function calculateProjectionModel(profiles, options = {}) {
        const completedMonths = Math.max(1, Math.min(12, Number(options.completedMonths) || 5));
        const scenarioName = SCENARIOS[options.scenario] ? options.scenario : 'expected';
        const scenario = SCENARIOS[scenarioName];
        const excludedProfiles = options.excludedProfiles || new Set();
        const customCategories = options.customCategories || new Map();
        const excludedWmus = new Set(options.excludedWmus || ['31']);
        const scheduledTons = Math.max(0, Number(options.scheduledTons) || 0);
        const potentialJobs = (options.potentialJobs || []).filter(job =>
            job &&
            !excludedWmus.has(String(job.wmu)) &&
            Number(job.totalTons) > 0
        );
        const years = [2023, 2024, 2025];
        const annual = {};
        const monthly = {};
        years.forEach(year => {
            annual[year] = { established: 0, emerging: 0, campaign: 0 };
            monthly[year] = {
                established: Array(12).fill(0),
                emerging: Array(12).fill(0),
                campaign: Array(12).fill(0)
            };
        });

        const ytd = { established: 0, emerging: 0, campaign: 0 };
        const health = { active: 0, declining: 0, dormant: 0 };
        const activeEstablished = [];

        profiles.forEach(profile => {
            if (excludedProfiles.has(profile.approval)) return;
            const category = customCategories.get(profile.approval) || profile.category;
            const type = categoryType(category);
            const current = profileMonthlyTons(profile, 2026, excludedWmus);
            const currentYtd = sum(current, completedMonths);
            ytd[type] += currentYtd;

            if (type === 'established') {
                const inScopeHistory = [2023, 2024, 2025].some(
                    year => sum(profileMonthlyTons(profile, year, excludedWmus)) > 0
                );
                if (!inScopeHistory && currentYtd <= 0) return;
                if (currentYtd <= 0) {
                    health.dormant++;
                } else {
                    health.active++;
                    const prior = profileMonthlyTons(profile, 2025, excludedWmus);
                    const priorYtd = sum(prior, completedMonths);
                    if (priorYtd > 0 && currentYtd < priorYtd * 0.85) health.declining++;
                    activeEstablished.push(profile);
                }
            }

            years.forEach(year => {
                const values = profileMonthlyTons(profile, year, excludedWmus);
                annual[year][type] += sum(values);
                for (let month = 0; month < 12; month++) monthly[year][type][month] += values[month];
            });
        });

        // Seasonality is based only on established profiles still active in the forecast year.
        const establishedFractions = years.map(year => {
            let fullYear = 0;
            let throughCutoff = 0;
            activeEstablished.forEach(profile => {
                const values = profileMonthlyTons(profile, year, excludedWmus);
                fullYear += sum(values);
                throughCutoff += sum(values, completedMonths);
            });
            return fullYear > 0 ? throughCutoff / fullYear : 0;
        }).filter(value => value > 0);

        const campaignFractions = years.map(year => {
            const fullYear = annual[year].campaign + annual[year].emerging;
            const throughCutoff = sum(monthly[year].campaign, completedMonths) +
                sum(monthly[year].emerging, completedMonths);
            return fullYear > 0 ? throughCutoff / fullYear : 0;
        }).filter(value => value > 0);

        const establishedFraction = average(establishedFractions);
        const campaignFraction = average(campaignFractions);
        const linear = {
            established: ytd.established * 12 / completedMonths,
            emerging: ytd.emerging * 12 / completedMonths,
            campaign: ytd.campaign * 12 / completedMonths
        };
        linear.base = linear.established + linear.emerging;
        linear.total = linear.base + linear.campaign;

        const raw = {
            established: establishedFraction > 0 ? ytd.established / establishedFraction : linear.established,
            emerging: linear.emerging,
            campaign: campaignFraction > 0 ? ytd.campaign / campaignFraction : linear.campaign
        };

        const pipeline = {
            gross: 0,
            weighted: 0,
            byType: { established: 0, emerging: 0, campaign: 0 },
            monthlyWeighted: Array(12).fill(0),
            jobs: potentialJobs.length
        };
        potentialJobs.forEach(job => {
            const gross = Math.max(0, Number(job.totalTons) || 0);
            const probability = Math.max(0, Math.min(100, Number(job.probability) || 0)) / 100;
            const weighted = gross * probability;
            const type = ['established', 'emerging', 'campaign'].includes(job.type)
                ? job.type
                : 'campaign';
            const startMonth = Math.max(1, Math.min(12, Number(job.startMonth) || completedMonths + 1));
            const duration = Math.max(1, Math.min(12 - startMonth + 1, Number(job.duration) || 1));
            pipeline.gross += gross;
            pipeline.weighted += weighted;
            pipeline.byType[type] += weighted;
            for (let month = startMonth - 1; month < startMonth - 1 + duration; month++) {
                pipeline.monthlyWeighted[month] += weighted / duration;
            }
        });
        const ytdTotal = ytd.established + ytd.emerging + ytd.campaign;
        const linearOrganicTotal = linear.total;
        linear.schedule_uplift = Math.max(0, scheduledTons - Math.max(0, linearOrganicTotal - ytdTotal));
        linear.potential = pipeline.weighted;
        linear.total = linearOrganicTotal + linear.schedule_uplift + linear.potential;

        const scenarioForecasts = {};
        Object.entries(SCENARIOS).forEach(([name, factors]) => {
            const pipelineFactor = name === 'conservative' ? 0.75 : name === 'upside' ? 1.25 : 1;
            const potential = Math.min(pipeline.gross, pipeline.weighted * pipelineFactor);
            const forecast = {
                established: raw.established * factors.established,
                emerging: raw.emerging * factors.emerging,
                campaign: raw.campaign * factors.campaign,
                potential
            };
            forecast.base = forecast.established + forecast.emerging;
            const organicTotal = forecast.base + forecast.campaign;
            forecast.schedule_uplift = Math.max(0, scheduledTons - Math.max(0, organicTotal - ytdTotal));
            forecast.total = organicTotal + forecast.schedule_uplift + forecast.potential;
            scenarioForecasts[name] = forecast;
        });
        const selected = scenarioForecasts[scenarioName];

        const establishedMonthlyFractions = Array(12).fill(0);
        years.forEach(year => {
            let yearTotal = 0;
            const yearMonths = Array(12).fill(0);
            activeEstablished.forEach(profile => {
                const values = profileMonthlyTons(profile, year, excludedWmus);
                yearTotal += sum(values);
                for (let month = 0; month < 12; month++) yearMonths[month] += values[month];
            });
            if (yearTotal > 0) {
                for (let month = 0; month < 12; month++) {
                    establishedMonthlyFractions[month] += yearMonths[month] / yearTotal / years.length;
                }
            }
        });

        const expectedMonthlyBase = establishedMonthlyFractions.map(
            fraction => selected.established * fraction + selected.emerging / 12
        );
        const ytdBase = ytd.established + ytd.emerging;
        const annualSplits = years.map(year => {
            const base = annual[year].established;
            const campaign = annual[year].campaign + annual[year].emerging;
            const total = base + campaign;
            return { year, established_tons: base, emerging_tons: 0, base_tons: base, campaign_tons: campaign, total_tons: total, base_pct: total > 0 ? base / total * 100 : 0 };
        });
        annualSplits.push({
            year: 2026,
            established_tons: ytd.established,
            emerging_tons: ytd.emerging,
            base_tons: ytdBase,
            campaign_tons: ytd.campaign,
            total_tons: ytdTotal,
            base_pct: ytdTotal > 0 ? ytdBase / ytdTotal * 100 : 0
        });

        return {
            completed_months: completedMonths,
            scenario: scenarioName,
            annual_splits: annualSplits,
            seasonality: {
                total_frac: ytdTotal > 0 ? (ytd.established * establishedFraction + ytd.campaign * campaignFraction) / ytdTotal : 0,
                base_frac: establishedFraction,
                campaign_frac: campaignFraction
            },
            health,
            pipeline,
            schedule: {
                estimated_tons: scheduledTons,
                uplift: selected.schedule_uplift
            },
            ytd,
            confidence: {
                low: scenarioForecasts.conservative.total,
                high: scenarioForecasts.upside.total
            },
            forecasts: {
                linear,
                scenarios: scenarioForecasts,
                seasonal: {
                    ...selected,
                    expected_monthly_base: expectedMonthlyBase,
                    monthly_fracs_base: establishedMonthlyFractions
                }
            },
            reconciliation: {
                ytd_actual: ytdTotal,
                remaining_forecast: Math.max(0, selected.total - ytdTotal),
                annual_forecast: selected.total
            }
        };
    }

    root.ProjectionEngine = { SCENARIOS, categoryType, calculateProjectionModel };
})(typeof window !== 'undefined' ? window : globalThis);
