import { Component, OnInit } from '@angular/core';
import { SidebarService } from './../sidebar/sidebar.service';
import { AuthService } from '../../services/auth.service';

@Component({
    selector: 'app-sidebar',
    templateUrl: './sidebar.component.html',
    styleUrls: ['./sidebar.component.scss']
})
export class SidebarComponent implements OnInit {

    constructor(public sidebarservice: SidebarService, public auth: AuthService) { }

    getSideBarSate() {
        return this.sidebarservice.getSidebarState();
    }

    isAdmin(): boolean {
        return this.auth.getCurrentUser()?.role === 'admin';
    }

    ngOnInit() {}
}
