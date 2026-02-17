

(function ($) {
  "use strict";


  /* ---------------------------------------------
  tabs
  --------------------------------------------- */
  var li_elements = document.querySelectorAll(".navigation ul li");
  var item_elements = document.querySelectorAll(".tab_pane");
  for (var i = 0; i < li_elements.length; i++) {
    li_elements[i].addEventListener("click", function () {
      if(this.getAttribute("data-li") == "true") { 
        li_elements.forEach(function (li) {
          li.classList.remove("active")
        })
        this.classList.add("active");

        var li_value = this.getAttribute("data-li");
        item_elements.forEach(function (tab_pane) {
          tab_pane.style.display = "none";
        })
        if (li_value == "profile-tab") {
          console.log('we are now at a profile tab');
          document.querySelector("." + li_value).style.display = "block";
        }
        else if (li_value == "profiles-tab") {
          console.log('we are on the profiles overview tab')
          document.querySelector("." + li_value).style.display = "block";
        }
        else if (li_value == "contacts-tab") {
          console.log('we are on the contacts overview tab')
          document.querySelector("." + li_value).style.display = "block";
        }
        else if (li_value == "contact-tab") {
          console.log('we are on the contact details tab')
          document.querySelector("." + li_value).style.display = "block";
        }
        else if (li_value == "logout-tab") {
          console.warn('TODO: logout')
        }
      }
    });
  }



  /* ---------------------------------------------
  sidebar
  --------------------------------------------- */
  $('.chat-item').on('click', function (e) {
    console.log('clicked chat-item');
    tabNavigation('profile-tab');
  });

  $('.chat-item').click(function () {
    console.log('clicked 2 chat-item');
    $('.chat-item').removeClass("open");
    $(this).addClass("open");
  });


  /* ---------------------------------------------
  Search form
  --------------------------------------------- */
  $('#search-avatar').keyup(function () {
    console.log('avatar search');
    var searchField = $('#search-avatar').val();
    var myExp = new RegExp('.*' + searchField + '.*', "i");
    $(".chat-item").hide();
    $(".chat-item").filter(function () {
      return myExp.test($(this).data("name"));
    }
    ).show();
  });


  /* ---------------------------------------------
  Search contacts form
  --------------------------------------------- */
  $('#search-contact').keyup(function () {
    var searchField = $('#search-contact').val();
    console.log('contact search: ' + searchField);

    var myExp = new RegExp('.*' + searchField + '.*', "i");
    $(".contact_details_container").hide().removeClass('d-flex');
    $(".contact_details_container").filter(function () {
      return myExp.test($(this).data("name"));
    }
    ).show().addClass('d-flex');
    filterContactList();
  });


  /* ---------------------------------------------
  Contact List
  --------------------------------------------- */
  $('.contact-details').on('click', function (e) {
    console.log('clicked contact-details');
    tabNavigation('contact-tab');
  });


  $('#browserbutton').on('click', function() {
    openBrowser($(this).data('id'),$(this).data('proxy'),$(this).data('language'));
  });



})(jQuery);

function filterContactList() {
  const listItems = document.querySelectorAll('.contact-list li');
  listItems.forEach(item => {
    const divs = item.querySelectorAll('div');
    const hasVisibleDiv = Array.from(divs).some(div => div.style.display !== 'none');
    if (!hasVisibleDiv) {
      item.style.display = 'none';
      const small = item.querySelector('small');
      small.style.display = 'none';
    } else {
      item.style.display = 'block';
      const small = item.querySelector('small');
      small.style.display = 'block';
    }
  });
}

function tabNavigation(selectedTabId) {
  var li_elements = document.querySelectorAll(".navigation ul li");
  var item_elements = document.querySelectorAll(".tab_pane");

  li_elements.forEach(function (li) {
    li.classList.remove("active");
  });

  var selectedTab = document.querySelector("[data-li='" + selectedTabId + "']");
  if (selectedTab) {
    selectedTab.classList.add("active");
  }

  item_elements.forEach(function (tab_pane) {
    tab_pane.style.display = "none";
  });

  var selectedTabPane = document.querySelector("." + selectedTabId);
  if (selectedTabPane) {
    console.log('we are now at a ' + selectedTabId + ' tab');
    selectedTabPane.style.display = "block";
  }
}