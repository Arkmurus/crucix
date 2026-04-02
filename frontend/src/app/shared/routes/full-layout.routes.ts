import { Routes } from '@angular/router';
import { AuthGuard } from '../../guards/auth.guard';
import { AdminGuard } from '../../guards/admin.guard';

//Route for content layout with sidebar, navbar and footer.

export const Full_ROUTES: Routes = [
    {
        path: 'dashboard',
        canActivate: [AuthGuard],
        loadChildren: () => import('../../dashboard/dashboard.module').then(m => m.DashboardModule)
    },

    {
        path: 'components',
        canActivate: [AuthGuard],
        loadChildren: () => import('../../components/components.module').then(m => m.ComponentsModule)
    },

    {
        path: 'forms',
        canActivate: [AuthGuard],
        loadChildren: () => import('../../forms/forms.module').then(m => m.FormsModule)
    },

    {
        path: 'tables',
        canActivate: [AuthGuard],
        loadChildren: () => import('../../tables/tables.module').then(m => m.TablesModule)
    },

    {
        path: 'widgets',
        canActivate: [AuthGuard],
        loadChildren: () => import('../../widgets/widgets.module').then(m => m.WidgetsModule)
    },

    {
        path: 'charts',
        canActivate: [AuthGuard],
        loadChildren: () => import('../../charts/charts.module').then(m => m.ChartsModule)
    },

    {
        path: 'faq',
        canActivate: [AuthGuard],
        loadChildren: () => import('../../faq/faq.module').then(m => m.FaqModule)
    },
    {
        path: 'profile',
        canActivate: [AuthGuard],
        loadChildren: () => import('../../profile/profile.module').then(m => m.ProfileModule)
    },
    {
        path: 'pricing',
        canActivate: [AuthGuard],
        loadChildren: () => import('../../pricing/pricing.module').then(m => m.PricingModule)
    },

    {
        path: 'downloads',
        canActivate: [AuthGuard],
        loadChildren: () => import('../../downloads/downloads.module').then(m => m.DownloadsModule)
    },

    {
        path: 'admin',
        canActivate: [AuthGuard, AdminGuard],
        loadChildren: () => import('../../admin/admin.module').then(m => m.AdminModule)
    }

];
