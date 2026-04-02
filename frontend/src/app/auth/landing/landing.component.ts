import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { MatModule } from 'src/app/appModules/mat.module';
import { AuthService } from '../../services/auth.service';

@Component({
  selector: 'app-landing',
  standalone: true,
  imports: [CommonModule, RouterModule, MatModule],
  templateUrl: './landing.component.html',
  styleUrl: './landing.component.scss'
})
export class LandingComponent implements OnInit {
  year = new Date().getFullYear();
  constructor(private auth: AuthService, private router: Router) {}

  ngOnInit(): void {
    // Already authenticated → go straight to dashboard
    if (this.auth.isLoggedIn()) {
      this.router.navigate(['/dashboard/brief']);
    }
  }
}
