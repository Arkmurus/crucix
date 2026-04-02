import { Routes } from '@angular/router';

//Route for content layout with sidebar, navbar and footer.

export const Full_ROUTES: Routes = [
    {
        path: 'dashboard',
        loadChildren: () => import('../../dashboard/dashboard.module').then(m => m.DashboardModule)
    },

    {
        path: 'components',
        loadChildren: () => import('../../components/components.module').then(m => m.ComponentsModule)
    },

    {
        path: 'forms',
        loadChildren: () => import('../../forms/forms.module').then(m => m.FormsModule)
    },

    {
        path: 'tables',
        loadChildren: () => import('../../tables/tables.module').then(m => m.TablesModule)
    },

    {
        path: 'widgets',
        loadChildren: () => import('../../widgets/widgets.module').then(m => m.WidgetsModule)
    },

    {
        path: 'charts',
        loadChildren: () => import('../../charts/charts.module').then(m => m.ChartsModule)
    },

    {
        path: 'faq',
        loadChildren: () => import('../../faq/faq.module').then(m => m.FaqModule)
    },
    {
        path: 'profile',
        loadChildren: () => import('../../profile/profile.module').then(m => m.ProfileModule)
    },
    {
        path: 'pricing',
        loadChildren: () => import('../../pricing/pricing.module').then(m => m.PricingModule)
    },

    {
        path: 'downloads',
        loadChildren: () => import('../../downloads/downloads.module').then(m => m.DownloadsModule)
    }
    
];