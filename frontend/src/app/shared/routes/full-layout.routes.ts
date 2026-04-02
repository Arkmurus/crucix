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
        path: 'profile',
        canActivate: [AuthGuard],
        loadChildren: () => import('../../profile/profile.module').then(m => m.ProfileModule)
    },

    {
        path: 'admin',
        canActivate: [AuthGuard, AdminGuard],
        loadChildren: () => import('../../admin/admin.module').then(m => m.AdminModule)
    }

];
