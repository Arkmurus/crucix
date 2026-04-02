import { Component, OnInit, OnDestroy } from '@angular/core';
import { Subscription } from 'rxjs';
import { SidebarService } from './../sidebar/sidebar.service';
import { AuthService } from '../../services/auth.service';
import { ChatService } from '../../services/chat.service';

@Component({
    selector: 'app-sidebar',
    templateUrl: './sidebar.component.html',
    styleUrls: ['./sidebar.component.scss']
})
export class SidebarComponent implements OnInit, OnDestroy {
    chatUnread = 0;
    private sub = new Subscription();

    constructor(
        public sidebarservice: SidebarService,
        public auth: AuthService,
        public chatService: ChatService
    ) {}

    getSideBarSate() {
        return this.sidebarservice.getSidebarState();
    }

    isAdmin(): boolean {
        return this.auth.getCurrentUser()?.role === 'admin';
    }

    ngOnInit() {
        this.chatService.connect();
        this.sub.add(
            this.chatService.unreadCount$.subscribe(n => { this.chatUnread = n; })
        );
    }

    ngOnDestroy() {
        this.sub.unsubscribe();
    }
}
