// Custom Scripts for Pelican Template //

jQuery(function($) {
    "use strict";

        // get the value of the bottom of the #main element by adding the offset of that element plus its height, set it as a variable
        var mainbottom = $('#main').offset().top;

        // on scroll,
        $(window).on('scroll',function(){

        // we round here to reduce a little workload
        stop = Math.round($(window).scrollTop());
        if (stop > mainbottom) {
            $('.navbar').addClass('past-main');
            $('.navbar').addClass('effect-main')
        } else {
            $('.navbar').removeClass('past-main');
       }

      });


  // Collapse navbar on click

   $(document).on('click.nav','.navbar-collapse.in',function(e) {
    if( $(e.target).is('a') ) {
    $(this).removeClass('in').addClass('collapse');
   }
  });


    /*-----------------------------------
    ----------- Scroll To Top -----------
    ------------------------------------*/

    $(window).on('scroll', function () {
      if ($(this).scrollTop() > 1000) {
          $('#back-top').fadeIn();
      } else {
          $('#back-top').fadeOut();
      }
    });
    // scroll body to 0px on click
    $('#back-top').on('click', function () {
      $('#back-top').tooltip('hide');
      $('body,html').animate({
          scrollTop: 0
      }, 1500);
      return false;
    });


    /*-------- Owl Carousel ---------- */

      $(".review-cards").owlCarousel({
        slideSpeed: 200,
        items: 1,
        singleItem: true,
        autoplay:true,
        autoplayTimeout:2000,
        autoplayHoverPause:true,
        pagination: false,
      });



  /* ------ jQuery for Easing min -- */
  (function($) {
    "use strict"; // Start of use strict

    // Smooth scrolling using jQuery easing
    $('a.js-scroll-trigger[href*="#"]:not([href="#"])').on('click', function () {
      if (location.pathname.replace(/^\//, '') == this.pathname.replace(/^\//, '') && location.hostname == this.hostname) {
        var target = $(this.hash);
        target = target.length ? target : $('[name=' + this.hash.slice(1) + ']');
        if (target.length) {
          $('html, body').animate({
            scrollTop: (target.offset().top - 54)
          }, 1000, "easeInOutExpo");
          return false;
        }
      }
    });

    // Closes responsive menu when a scroll trigger link is clicked
    $('.js-scroll-trigger').on('click', function() {
      $('.navbar-collapse').collapse('hide');
    });

    // Activate scrollspy to add active class to navbar items on scroll
    $('body').scrollspy({
      target: '#mainNav',
      offset: 54
    });

  })(jQuery); // End of use strict


/* --------- Wow Init ------ */

  new WOW().init();


  /* ----- Counter Up ----- */

$('.counter').counterUp({
		delay: 10,
		time: 1000
});

/*----- Preloader ----- */

    $(window).on('load', function() {
		setTimeout(function() {
        $('#loading').fadeOut('slow', function() {
        });
      }, 3000);
    });


/*----- ARIA access request form ----- */

$(document).ready(function() {
  var $form = $('#lead-form');
  var $response = $('#lead-response');

  $form.on('submit', function(event) {
    event.preventDefault();
    var name = $.trim($('#lead-name').val());
    var email = $.trim($('#lead-email').val());
    var useCase = $.trim($('#lead-use-case').val());
    // R-F3531 — organisation, jurisdiction and role are graded by the intake
    // assessment, so they have to be collected and sent. They were neither.
    var company = $.trim($('#lead-company').val());
    var country = $.trim($('#lead-country').val());
    var role = $.trim($('#lead-role').val());
    var $button = $form.find('button[type="submit"]');

    $response.removeClass('is-success is-error');
    if (!name || !email || email.indexOf('@') < 1 || !useCase) {
      $response.addClass('is-error').text('Please enter your name, a valid work email and your primary use case.');
      return;
    }
    if (!company || !country || !role) {
      $response.addClass('is-error').text('Please add your organisation, primary jurisdiction and role so we can assess the request.');
      return;
    }

    $button.prop('disabled', true).text('Sending…');
    $.ajax({
      url: '/api/leads',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({
        name: name,
        email: email,
        use_case: useCase,
        company: company,
        country: country,
        role: role
      })
    }).done(function(data) {
      $form[0].reset();
      // Say what actually happened (§22). "Check your email" is a lie when the
      // confirmation could not be sent, and it would leave the visitor waiting
      // for a message that is never coming.
      var verification = data && data.verification;
      var message = 'Thank you. Your request has been recorded.';
      if (verification === 'sent') {
        message += ' Please confirm your address using the link we have just emailed you.';
      } else if (verification === 'not_sent') {
        message += ' We could not send the confirmation email — we will follow up directly.';
      }
      $response.addClass('is-success').text(message);
    }).fail(function(xhr) {
      var message = xhr.responseJSON && xhr.responseJSON.error;
      $response.addClass('is-error').text(message || 'We could not record your request. Please try again shortly.');
    }).always(function() {
      $button.prop('disabled', false).text('Request access');
    });
  });
});

});
