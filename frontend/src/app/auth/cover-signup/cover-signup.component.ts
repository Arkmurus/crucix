import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-cover-signup',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './cover-signup.component.html',
  styleUrl: './cover-signup.component.scss'
})
export class CoverSignupComponent implements OnInit {
  constructor(private router: Router) {}
  ngOnInit(): void { this.router.navigate(['/auth/sign-up']); }
}
